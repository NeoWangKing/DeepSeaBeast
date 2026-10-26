"""运行时：收消息 → 管线判断 → 组装上下文 → 工具循环 → 交回 channel。

上下文（M4.5 起）分三层，交给 ContextBundle：
  self（她是谁）/ scope（这一处的记忆）/ recent（刚聊了什么）
插件通过 ContextRegistry 往里加料；核心保证顺序与预算。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
from dataclasses import replace
from pathlib import Path

from .agent import run_turn
from .channel import ChatRef, InboundMessage, Message, OutboundMessage, SendResult, Sender
from .context import ContextBundle, ContextRegistry
from .llm import LLMConfig
from .memory import MemoryStore
from .pipeline import GateConfig, Pipeline
from .scoperef import ScopeRegistry, scope_ref
from .policy import resolve
from .prompt import build_scene, render
from .scope import Scope
from .face_stats import FaceStats
from .store import RecentStore
from .tools import Turn
from .turn import TurnConfig, TurnContext, new_turn_id

log = logging.getLogger("runtime")


class Runtime:
    def __init__(self, channel, gate: GateConfig | None = None,
                 owner_ids: list[int] | None = None, llm: LLMConfig | None = None,
                 reply_fn=None, plugins=None, data_dir: str = "data",
                 budget_tokens: int = 6000, turn_cfg: TurnConfig | None = None,
                 todo_cfg: dict | None = None) -> None:
        self.channel = channel
        self.owner_ids = set(owner_ids or [])
        self.pipeline = Pipeline(gate or GateConfig())
        self.llm = llm or LLMConfig()
        self.reply_fn = reply_fn or self._llm_reply
        self._reply_takes_ctx = _accepts_ctx(self.reply_fn)
        self.turn_cfg = turn_cfg or TurnConfig()
        from .todos import TodoStore
        tc = todo_cfg or {}
        self.todos = TodoStore(os.path.join(data_dir, "todos"),
                               max_open=int(tc.get("max_open", 12)),
                               stale_days=int(tc.get("stale_days", 7)),
                               remind_per_day=int(tc.get("remind_per_day", 3)))
        self.todo_tick_sec = float(tc.get("tick_sec", 60))
        self._chats: dict[str, object] = {}     # 不透明引用 → chat（到点唤醒要发回去）
        self._last_said: dict[str, str] = {}    # 每个会话最近一轮她说了什么
        self.gate = gate or GateConfig()        # 门控配置（策略/开关都从这来）
        self._policy_of: dict[str, object] = {}  # 会话 → ChatPolicy
        self._hints: dict[str, list[str]] = {}   # 会话 → 最近一次的场面提示
        self._names_cache: dict[str, tuple[float, list[str]]] = {}   # 会话 → 她认的称呼
        self.scopes = ScopeRegistry(data_dir)
        self._active: dict[str, TurnContext] = {}     # scope_key → 正在跑的回合
        self._last_decision: dict | None = None       # 最近一个回合的 decide 结果
        self.plugins = plugins
        self.persona = ""
        self.rules = ["一次最多说 4 条消息，别刷屏",
                      "别把代码整段贴进聊天",
                      "别单发「行吧」「好的」「我来」这种铺垫——要说的和要发的合并成一条，省回合",
                      "你会做什么只看给你的工具；工具里没有的事（画图、生图、搜索、写文件…）"
                      "就是做不到，直接说做不到，别答应、别假装做了"]
        self.budget_tokens = int(budget_tokens)
        self.memory = MemoryStore(Path(data_dir) / "memory")
        self.recent = RecentStore(Path(data_dir) / "recent")
        self.faces = FaceStats(Path(data_dir) / "memory")
        self.registry = ContextRegistry()
        if plugins is not None:
            try:
                for p in plugins.context_providers():
                    self.registry.add(p)
                    log.info("上下文插件已注册：%s（priority=%d, persona=%s）",
                             p.name, p.priority, p.can_persona)
            except Exception:
                log.exception("注册上下文插件失败")
        self._flush: dict[str, asyncio.Task] = {}

    # ── 策略辅助 ──
    def _policy(self, chat) -> object:
        return resolve(self.gate.default_preset, self.gate.chats, chat.kind, chat.id)

    def _store_allowed(self, chat) -> bool:
        """memory=off 的会话：不写 recent、不发插件事件。"""
        return self._policy(chat).store_memory

    async def _persona_names(self, msg) -> list[str]:
        """这个会话里她认哪些称呼——**跟着人格卡走**（缓存 60 秒）。

        为什么要核心来判断：称呼是人格的属性，不是通道的属性。
        @ 仍然由通道处理（QQ 群名片改不了），这里只管"文字里叫她的名字"。
        """
        if self.plugins is None or not self.plugins.has_plugin("personas"):
            return []
        key = self._ref_of(msg)
        now = time.time()
        cached = self._names_cache.get(key)
        if cached and now - cached[0] < 60:
            return list(cached[1])
        try:
            info = (self.channel.scope_info(msg.chat)
                    if hasattr(self.channel, "scope_info") else {})
            res = await self.plugins.request("personas", "names", {
                "scope": {"kind": msg.chat.kind, "id": msg.chat.id, "key": key,
                          "label": str(info.get("label") or ""),
                          "name": str(info.get("name") or "")},
                "channel": {"name": getattr(self.channel, "name", "")},
            }, timeout=5)
            names = [str(x) for x in ((res or {}).get("aliases") or []) if x]
        except Exception as e:
            log.debug("取人格称呼失败（忽略）：%r", e)
            names = []
        self._names_cache[key] = (now, names)
        return names

    def _hints_of(self, ref: str) -> list[str]:
        return list(self._hints.get(ref) or [])

    def _identity_names(self, chat) -> list[str]:
        """这个会话里别人可能怎么叫她（打分和"提到名字"信号用）。"""
        try:
            info = self.channel.identity(chat) if hasattr(self.channel, "identity") else {}
            return [str(x) for x in (info.get("aliases") or []) if x]
        except Exception:
            return []

    # ── 收 ──
    async def on_message(self, msg: InboundMessage) -> None:
        now = time.time()
        self._learn_faces(msg)
        key = self._ref_of(msg)
        _info = (self.channel.scope_info(msg.chat)
                 if hasattr(self.channel, "scope_info") else {})
        pnames = await self._persona_names(msg)
        if pnames and not msg.addressed:
            hit = next((n for n in pnames if n and n in msg.text), "")
            if hit:
                msg = replace(msg, addressed=True, addressed_by="name")
                log.info("[%s] 文字里叫了她：%s", key, hit)
        store = self._store_allowed(msg.chat)
        if store:
            await self._emit("message.received", {
            # 插件只拿不透明引用 + 人话标签，不拿平台 id
                "scope": {"ref": key, "kind": msg.chat.kind,
                          "label": str(_info.get("label") or ""),
                          "name": str(_info.get("name") or "")},
                "sender": {"id": msg.sender.id, "name": msg.sender.name or ""},
                "text": msg.text, "addressed": bool(msg.addressed), "ts": now,
                # 带媒体清单（贴图/图片等）：插件想收就收，核心不判断用途
                "media": _media_of(msg)})
        # 同一个人又说话了：上一条"等着重放"的计时直接作废，由这条重新计时
        pending_flush = self._flush.get(key)
        if pending_flush is not None and not pending_flush.done():
            st = self.pipeline.state_for(key)
            if st.merge_sender in (0, msg.sender.id):
                pending_flush.cancel()
                self._flush.pop(key, None)
        prev = self._active.get(key)
        if prev is not None and not prev.finished:
            prev.cancel("新消息打断")
            log.info("[%s] 回合 %s 被打断（她正在说，群里又来了话）", key, prev.id)

        policy = resolve(self.gate.default_preset, self.gate.chats, msg.chat.kind, msg.chat.id)
        self._policy_of[key] = policy
        ctx = TurnContext(id=new_turn_id(), scope_key=key, cfg=self.turn_cfg, chat=msg.chat)
        self._active[key] = ctx
        try:
            out = self.pipeline.run(msg, now,
                                    identity_names=pnames or self._identity_names(msg.chat))
            self._hints[key] = list(getattr(out, "hints", []) or [])
            log.info("[%s] 回合 %s 判断 %r → %s（%s%s）", key, ctx.id, msg.text[:24],
                     out.kind, out.stage or "-", ("：" + out.reason) if out.reason else "")
            if out.kind == "defer":
                self._schedule_replay(msg, out.text, now)   # 稍后重放，不算空闲
                return
            if out.kind != "reply":
                return
            if out.text and out.text.strip() != msg.text.strip():
                msg = replace(msg, message=Message.from_text(out.text))   # 用合并后的整体
            await self._notify("on_message", msg)
            said = await self._call_reply(msg, ctx)
            self._last_said[key] = said or ""
            if store:
                self._record_turn(msg, said)
            if said:
                self.pipeline.note_replied(msg, time.time())
                log.info("[%s] 回合 %s 完成：%r", key, ctx.id, said[:40])
            elif self._said_wait() and not _is_recheck(msg):
                self._schedule_recheck(msg)      # 她说"先等等" → 过几秒再决定一次
            else:
                self._mark_idle(msg.chat)
        finally:
            ctx.finished = True
            if self._active.get(key) is ctx:
                self._active.pop(key, None)
            log.info("[%s] %s", key, ctx.describe())
            if not self._has_pending(key):
                self._mark_idle(msg.chat)

    # ── 上下文组装 ──
    async def build_context(self, msg: InboundMessage) -> ContextBundle:
        scope = Scope.from_chat(msg.chat)
        ref = self._ref_of(msg)
        _info = (self.channel.scope_info(msg.chat)
                 if hasattr(self.channel, "scope_info") else {})
        b = ContextBundle(scope_key=ref, persona=self.persona,
                          budget_tokens=self.budget_tokens, current=msg,
                          scope_kind=scope.kind, scope_id=scope.id,
                          sender_id=msg.sender.id,
                          sender_name=msg.sender.name or "",
                          channel_name=(self.channel.name_for(msg.chat)
                                        if hasattr(self.channel, "name_for") else ""),
                          scope_label=str(_info.get("label") or ""),
                          scope_name=str(_info.get("name") or ""))
        b.abilities = sorted(self.abilities_for(msg.chat))
        prof = self._channel_profile(msg.chat)
        b.style = str(prof.get("style") or "")
        inline = prof.get("inline") or {}
        b.mention = bool(inline.get("mention"))
        b.markdown = bool(inline.get("markdown"))
        b.max_chars = int((prof.get("limits") or {}).get("max_chars", 4500) or 4500)
        if "face" in b.abilities:
            b.faces_top = self.faces.top(ref, fallback=list(inline.get("faces") or [])[:12])
        from .capability import describe_abilities
        _can, _cannot = describe_abilities(b.abilities)
        b.rules = list(self.rules)
        if _cannot:
            b.rules.append("当前会话做不到：%s。做不到就直接说做不到，不许答应、不许假装做了。"
                           % "、".join(_cannot))
        parts = self.memory.self_parts()
        b.self_knowledge = [parts[k] for k in ("identity", "owner", "lessons") if k in parts]
        for text in self.memory.scope_parts(scope).values():
            b.add_knowledge(text, 20)
        where = self.channel.describe(msg.chat) if hasattr(self.channel, "describe") else ""
        _ident = (self.channel.identity(msg.chat)
                  if hasattr(self.channel, "identity") else {})
        b.scene = build_scene(msg, where=where,
                              self_name=str(_ident.get("name") or ""),
                              aliases=list(_ident.get("aliases") or []),
                              hints=self._hints_of(ref))
        b.abilities = sorted(self.abilities_for(msg.chat))
        prof = self._channel_profile(msg.chat)
        b.style = str(prof.get("style") or "")
        inline = prof.get("inline") or {}
        b.mention = bool(inline.get("mention"))
        b.markdown = bool(inline.get("markdown"))
        b.max_chars = int((prof.get("limits") or {}).get("max_chars", 4500) or 4500)
        if "face" in b.abilities:
            b.faces_top = self.faces.top(ref, fallback=list(inline.get("faces") or [])[:12])
        _pol = self._policy(msg.chat)
        b.recent = self.recent.get(ref) if _pol.history else []   # history=off 不带聊天记录
        await self.registry.collect(b)
        b.trim()
        return b

    # ── 回复 ──
    async def _call_reply(self, msg: InboundMessage, ctx: TurnContext) -> str:
        try:
            if self._reply_takes_ctx:
                return await self.reply_fn(msg, ctx)
            return await self.reply_fn(msg)
        except Exception:
            log.exception("[%s] 生成回复失败，这条不回", ctx.id)
            return ""

    async def _llm_reply(self, msg: InboundMessage, ctx: TurnContext | None = None) -> str:
        scope = Scope.from_chat(msg.chat)
        ref = self._ref_of(msg)
        bundle = await self.build_context(msg)
        messages = render(bundle)
        if hasattr(self.channel, "reset_pacing"):
            self.channel.reset_pacing(msg.chat)      # 新一轮第一条不等待
        turn = Turn(self.channel, msg, self.llm, persona=bundle.persona,
                    plugins=self.plugins, ctx=ctx,
                    abilities=self.abilities_for(msg.chat),
                    faces_catalog=self._face_catalog(msg.chat),
                    todos=self.todos, scope_key=self._ref_of(msg),
                    tools_mode=self._policy(msg.chat).tools)
        line = turn.skill_text()
        if line:                       # 能力清单放在当前消息之前，紧贴着这轮
            messages.insert(len(messages) - 1, {"role": "system", "content": line})
        await run_turn(turn, messages)
        self._last_decision = dict(turn.decision) if turn.decision else None
        if turn.decision:
            log.info("她的决定：%s（%s）", turn.decision.get("action"),
                     turn.decision.get("intent") or "")
        return "\n".join(turn.sent)

    def _has_pending(self, key: str) -> bool:
        t = self._flush.get(key)
        return bool(t is not None and not t.done())

    def _mark_idle(self, chat) -> None:
        fn = getattr(self.channel, "idle", None)
        if fn is not None:
            try:
                fn(chat)
            except Exception:
                pass

    def _said_wait(self) -> bool:
        d = self._last_decision or {}
        return str(d.get("action") or "") == "wait"

    def _schedule_recheck(self, msg: InboundMessage) -> None:
        """decide 说了 wait（先等等）→ 过 wait_recheck_sec 秒再决定一次。

        只重判一次：第二次还是 wait 就当成"不说话"，避免无限等下去。
        """
        delay = float(getattr(self.turn_cfg, "wait_recheck_sec", 5.0))
        key = self._ref_of(msg)
        log.info("[%s] 她说先等等，%.0fs 后再决定一次", key, delay)

        async def _later() -> None:
            try:
                await asyncio.sleep(delay)
                cur = self._active.get(key)
                if cur is not None and not cur.finished:
                    return                    # 已经有新回合在跑了，不打断
                again = replace(msg, raw={"_deepsea_recheck": True})
                await self.on_message(again)
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("重判失败")

        asyncio.create_task(_later())

    def _channel_profile(self, chat) -> dict:
        """取通道的 profile；manager 收 chat 参数，单个 adapter 可能不收。"""
        fn = getattr(self.channel, "profile", None)
        if fn is None:
            return {}
        try:
            return dict(fn(chat) or {})
        except TypeError:
            return dict(fn() or {})

    def _face_catalog(self, chat) -> list[str]:
        return list((self._channel_profile(chat).get("inline") or {}).get("faces") or [])

    def _ref_of(self, msg: InboundMessage) -> str:
        """给这条消息的会话打上不透明引用（核心内部只用它），并登记人话身份。"""
        chat = msg.chat
        channel_name = (self.channel.name_for(chat)
                        if hasattr(self.channel, "name_for")
                        else getattr(self.channel, "name", ""))
        if not getattr(chat, "ref", ""):
            chat.ref = scope_ref(channel_name, chat.key)
        info = (self.channel.scope_info(chat)
                if hasattr(self.channel, "scope_info") else {})
        self.scopes.remember(chat.ref, channel_name, chat.key, kind=chat.kind,
                             label=str(info.get("label") or ""),
                             name=str(info.get("name") or ""))
        self._chats[chat.ref] = chat
        return chat.ref

    async def watch_todos(self) -> None:
        """后台盯着待办的到期时间：到点了主动开口提醒（每个会话每天有次数上限）。"""
        log.info("待办提醒已启动（按最近到期时间精确唤醒，最长 %.0fs 查一次；每会话每天最多 %d 次）",
                 self.todo_tick_sec, self.todos.remind_per_day)
        while True:
            try:
                # 有明确的到期时间就睡到那一刻（最多睡 tick_sec），否则按 tick 兜底
                wait = max(1.0, self.todo_tick_sec)
                nxt = self.todos.next_due_at()
                if nxt is not None:
                    wait = min(wait, max(0.5, nxt - time.time() + 0.2))
                await asyncio.sleep(wait)
                await self._fire_due_todos()
            except asyncio.CancelledError:
                log.info("待办提醒已停止")
                raise
            except Exception as e:
                log.warning("待办提醒出错：%r", e)

    async def _fire_due_todos(self) -> None:
        for scope, todo in self.todos.due_now():
            chat = self._chats.get(scope)
            if chat is None:
                continue                      # 没见过的会话，不知道怎么发
            active = self._active.get(scope)
            if active is not None and not active.finished:
                continue                      # 她正在说话，下一轮再说
            if not self.todos.mark_reminded(scope, todo.id):
                continue                      # 今天提醒次数用完了
            log.info("[%s] 到点提醒：%s", scope, todo.text)
            msg = InboundMessage(
                chat=chat, sender=Sender(0, "系统提醒"),
                message=Message.from_text(""), message_id="todo-" + todo.id,
                addressed=True, addressed_by="reminder",
                raw={"_deepsea_reminder": "你之前记下要做：%s（%s）。现在到点了。"
                                          % (todo.text, todo.id)})
            await self.on_message(msg)
            if self._last_said.get(scope):
                # 提醒到了、她也说了 → 这件事就算办了，别让待办一直挂着占名额
                ok, _msg = self.todos.done(scope, todo.id)
                if ok:
                    log.info("[%s] 提醒已送达，待办 %s 自动收掉", scope, todo.id)

    def _learn_faces(self, msg: InboundMessage) -> None:
        """统计群友用了哪些表情（只记别人发的，不记她自己发的）。"""
        try:
            names = [str(s.data.get("name") or "") for s in msg.message.segments
                     if getattr(s, "kind", "") == "face"]
            names = [n for n in names if n]
            if names:
                self.faces.count(self._ref_of(msg), names)
        except Exception:
            log.debug("表情统计失败", exc_info=True)

    def _record_turn(self, msg: InboundMessage, said: str) -> None:
        """把这一轮写进上下文：**用户先说，她再说**。

        顺序反了的话，模型会以为"她说完用户才接话"，下一轮就把自己当主导者乱接
        （2026-10-23 实测：问"在吗"，她继续上一轮的"损人"）。
        """
        key = self._ref_of(msg)
        who = msg.sender.name or "对方"
        self.recent.append(key, who, msg.text, msg.sender.id, time.time())
        for text in [x for x in (said or "").splitlines() if x.strip()]:
            self.recent.append(key, "我", text, 0, time.time())

    async def _emit(self, event: str, data: dict) -> None:
        """把事件推给订阅的插件（单向，失败不影响主流程）。"""
        if self.plugins is None:
            return
        try:
            await self.plugins.emit(event, data)
        except Exception:
            log.debug("事件 %s 分发失败", event, exc_info=True)

    async def _notify(self, hook: str, *args) -> None:
        for p in getattr(self.registry, "_providers", []):
            fn = getattr(p, hook, None)
            if fn is None:
                continue
            try:
                await fn(*args)
            except Exception:
                log.exception("上下文插件 %s 的 %s 出错", getattr(p, "name", "?"), hook)

    # ── 连发合并的延后重放 ──
    def _schedule_replay(self, msg: InboundMessage, merged: str, now: float) -> None:
        key = self._ref_of(msg)
        old = self._flush.get(key)
        if old is not None and not old.done():
            old.cancel()

        async def _later() -> None:
            try:
                await asyncio.sleep(self.pipeline.cfg.typing_wait_sec)
                state = self.pipeline.state_for(key)
                if not state.merge_buf or state.merge_buf.strip() != merged.strip():
                    return
                replay = InboundMessage(chat=msg.chat, sender=msg.sender,
                                        message=Message.from_text(state.merge_buf),
                                        message_id=msg.message_id,
                                        addressed=msg.addressed,
                                        addressed_by=msg.addressed_by, raw=msg.raw)
                state.merge_buf = ""
                out = self.pipeline.run(replay, time.time(), skip=("merge",))
                log.info("重放合并 [%s] %r → %s（%s）", key, replay.text[:30],
                         out.kind, out.stage or "-")
                if out.kind == "reply":
                    rctx = TurnContext(id=new_turn_id(), scope_key=key,
                                       cfg=self.turn_cfg, chat=replay.chat)
                    self._active[key] = rctx
                    try:
                        if out.text and out.text.strip() != replay.text.strip():
                            replay = replace(replay, message=Message.from_text(out.text))
                        said = await self._call_reply(replay, rctx)
                        self._record_turn(replay, said)
                        if said:
                            self.pipeline.note_replied(replay, time.time())
                    finally:
                        rctx.finished = True
                        if self._active.get(key) is rctx:
                            self._active.pop(key, None)
                        self._mark_idle(replay.chat)
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("重放失败")

        self._flush[key] = asyncio.create_task(_later())

    # ── 发 ──
    def abilities_for(self, chat: ChatRef) -> set[str]:
        fn = getattr(self.channel, "abilities_for", None)
        return set(fn(chat)) if fn else set(getattr(self.channel, "abilities", set()))

    async def say(self, chat: ChatRef, text: str, quote_id: str = "",
                  at_user: int = 0) -> SendResult:
        out = OutboundMessage(chat=chat, message=Message.from_text(text),
                              quote_id=quote_id, at_user=at_user)
        res = await self.channel.send(out)
        if not res.ok:
            log.warning("发送失败：%s", res.error)
        return res


def _media_of(msg) -> list[dict]:
    """把消息里的媒体段整理成给插件的清单。

    只给"引用/地址"，不给字节：插件自己去取，核心不碰二进制。
    """
    out: list[dict] = []
    try:
        segs = getattr(getattr(msg, "message", None), "segments", []) or []
    except Exception:
        return out
    for seg in segs:
        kind = str(getattr(seg, "kind", "") or "")
        if kind not in ("sticker", "image"):
            continue
        data = dict(getattr(seg, "data", {}) or {})
        out.append({"kind": kind,
                    "name": str(data.get("name") or ""),
                    "url": str(data.get("url") or ""),
                    "file": str(data.get("file") or "")})
    return out


def _accepts_ctx(fn) -> bool:
    """注入的 reply_fn 若声明了第二个参数，就把 TurnContext 传给它。"""
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return False
    pos = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return len(pos) >= 2 or any(p.kind == p.VAR_POSITIONAL for p in params)


def _is_recheck(msg: InboundMessage) -> bool:
    return bool((msg.raw or {}).get("_deepsea_recheck")) if isinstance(msg.raw, dict) else False
