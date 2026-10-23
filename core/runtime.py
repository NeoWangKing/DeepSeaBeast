"""运行时：收消息 → 管线判断 → 组装上下文 → 工具循环 → 交回 channel。

上下文（M4.5 起）分三层，交给 ContextBundle：
  self（她是谁）/ scope（这一处的记忆）/ recent（刚聊了什么）
插件通过 ContextRegistry 往里加料；核心保证顺序与预算。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import replace
from pathlib import Path

from .agent import run_turn
from .channel import ChatRef, InboundMessage, Message, OutboundMessage, SendResult
from .context import ContextBundle, ContextRegistry
from .llm import LLMConfig
from .memory import MemoryStore
from .pipeline import GateConfig, Pipeline
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
                 budget_tokens: int = 6000, turn_cfg: TurnConfig | None = None) -> None:
        self.channel = channel
        self.owner_ids = set(owner_ids or [])
        self.pipeline = Pipeline(gate or GateConfig())
        self.llm = llm or LLMConfig()
        self.reply_fn = reply_fn or self._llm_reply
        self._reply_takes_ctx = _accepts_ctx(self.reply_fn)
        self.turn_cfg = turn_cfg or TurnConfig()
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

    # ── 收 ──
    async def on_message(self, msg: InboundMessage) -> None:
        now = time.time()
        self._learn_faces(msg)
        await self._emit("message.received", {
            "scope": {"kind": msg.chat.kind, "id": msg.chat.id, "key": msg.chat.key},
            "sender": {"id": msg.sender.id, "name": msg.sender.name or ""},
            "text": msg.text, "at_me": bool(msg.at_me), "ts": now})
        key = msg.chat.key
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

        ctx = TurnContext(id=new_turn_id(), scope_key=key, cfg=self.turn_cfg, chat=msg.chat)
        self._active[key] = ctx
        try:
            out = self.pipeline.run(msg, now)
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
        _info = (self.channel.scope_info(msg.chat)
                 if hasattr(self.channel, "scope_info") else {})
        b = ContextBundle(scope_key=scope.key, persona=self.persona,
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
            b.faces_top = self.faces.top(scope.key, fallback=list(inline.get("faces") or [])[:12])
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
        b.scene = build_scene(msg, where=where,
                              self_account=str(getattr(self.channel, "self_account", "") or ""))
        b.abilities = sorted(self.abilities_for(msg.chat))
        prof = self._channel_profile(msg.chat)
        b.style = str(prof.get("style") or "")
        inline = prof.get("inline") or {}
        b.mention = bool(inline.get("mention"))
        b.markdown = bool(inline.get("markdown"))
        b.max_chars = int((prof.get("limits") or {}).get("max_chars", 4500) or 4500)
        if "face" in b.abilities:
            b.faces_top = self.faces.top(scope.key, fallback=list(inline.get("faces") or [])[:12])
        b.recent = self.recent.get(scope.key)
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
        bundle = await self.build_context(msg)
        messages = render(bundle)
        if hasattr(self.channel, "reset_pacing"):
            self.channel.reset_pacing(msg.chat)      # 新一轮第一条不等待
        turn = Turn(self.channel, msg, self.llm, persona=bundle.persona,
                    plugins=self.plugins, ctx=ctx,
                    abilities=self.abilities_for(msg.chat),
                    faces_catalog=self._face_catalog(msg.chat))
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
        key = msg.chat.key
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

    def _learn_faces(self, msg: InboundMessage) -> None:
        """统计群友用了哪些表情（只记别人发的，不记她自己发的）。"""
        try:
            names = [str(s.data.get("name") or "") for s in msg.message.segments
                     if getattr(s, "kind", "") == "face"]
            names = [n for n in names if n]
            if names:
                self.faces.count(msg.chat.key, names)
        except Exception:
            log.debug("表情统计失败", exc_info=True)

    def _record_turn(self, msg: InboundMessage, said: str) -> None:
        """把这一轮写进上下文：**用户先说，她再说**。

        顺序反了的话，模型会以为"她说完用户才接话"，下一轮就把自己当主导者乱接
        （2026-10-23 实测：问"在吗"，她继续上一轮的"损人"）。
        """
        key = msg.chat.key
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
        key = msg.chat.key
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
                                        message_id=msg.message_id, at_me=msg.at_me, raw=msg.raw)
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
