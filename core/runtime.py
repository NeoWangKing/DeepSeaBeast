"""运行时：收消息 → 管线判断 → 组装上下文 → 工具循环 → 交回 channel。

上下文（M4.5 起）分三层，交给 ContextBundle：
  self（她是谁）/ scope（这一处的记忆）/ recent（刚聊了什么）
插件通过 ContextRegistry 往里加料；核心保证顺序与预算。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from .agent import run_turn
from .channel import ChatRef, InboundMessage, Message, OutboundMessage, SendResult
from .context import ContextBundle, ContextRegistry
from .llm import LLMConfig
from .memory import MemoryStore
from .pipeline import GateConfig, Pipeline
from .prompt import build_scene, render
from .scope import Scope
from .store import RecentStore
from .tools import Turn

log = logging.getLogger("runtime")


class Runtime:
    def __init__(self, channel, gate: GateConfig | None = None,
                 owner_ids: list[int] | None = None, llm: LLMConfig | None = None,
                 reply_fn=None, plugins=None, data_dir: str = "data",
                 budget_tokens: int = 6000) -> None:
        self.channel = channel
        self.owner_ids = set(owner_ids or [])
        self.pipeline = Pipeline(gate or GateConfig())
        self.llm = llm or LLMConfig()
        self.reply_fn = reply_fn or self._llm_reply
        self.plugins = plugins
        self.persona = ""
        self.rules = ["一次最多说 4 条消息，别刷屏", "别把代码整段贴进聊天"]
        self.budget_tokens = int(budget_tokens)
        self.memory = MemoryStore(Path(data_dir) / "memory")
        self.recent = RecentStore(Path(data_dir) / "recent")
        self.registry = ContextRegistry()
        self._flush: dict[str, asyncio.Task] = {}

    # ── 收 ──
    async def on_message(self, msg: InboundMessage) -> None:
        now = time.time()
        out = self.pipeline.run(msg, now)
        log.info("判断 [%s] %r → %s（%s%s）", msg.chat.key, msg.text[:30], out.kind,
                 out.stage or "-", ("：" + out.reason) if out.reason else "")
        if out.kind == "defer":
            self._schedule_replay(msg, out.text, now)
            return
        if out.kind != "reply":
            return
        said = await self._reply(msg)
        if said:
            self.pipeline.note_replied(msg, time.time())
            log.info("已回复 [%s] %r", msg.chat.key, said[:40])

    # ── 上下文组装 ──
    async def build_context(self, msg: InboundMessage) -> ContextBundle:
        scope = Scope.from_chat(msg.chat)
        b = ContextBundle(scope_key=scope.key, persona=self.persona,
                          budget_tokens=self.budget_tokens, current=msg)
        b.rules = list(self.rules)
        parts = self.memory.self_parts()
        b.self_knowledge = [parts[k] for k in ("identity", "owner", "lessons") if k in parts]
        for text in self.memory.scope_parts(scope).values():
            b.add_knowledge(text, 20)
        b.scene = build_scene(msg, getattr(self.channel, "name", ""),
                              int(getattr(self.channel, "bot_id", 0) or 0))
        b.recent = self.recent.get(scope.key)
        await self.registry.collect(b)
        b.trim()
        return b

    # ── 回复 ──
    async def _reply(self, msg: InboundMessage) -> str:
        try:
            return await self.reply_fn(msg)
        except Exception:
            log.exception("生成回复失败，这条不回")
            return ""

    async def _llm_reply(self, msg: InboundMessage) -> str:
        scope = Scope.from_chat(msg.chat)
        bundle = await self.build_context(msg)
        messages = render(bundle)
        await self._notify("on_message", msg)
        turn = Turn(self.channel, msg, self.llm, persona=bundle.persona,
                    plugins=self.plugins)
        await run_turn(turn, messages, max_rounds=6)
        if turn.decision:
            log.info("她的决定：%s（%s）", turn.decision.get("action"),
                     turn.decision.get("intent") or "")
        for text in turn.sent:
            self.recent.append(scope.key, "我", text, 0, time.time())
            await self._notify("on_reply", msg, text)
        if turn.sent:
            who = msg.sender.name or "对方"
            self.recent.append(scope.key, who, msg.text, msg.sender.id, time.time())
        return "\n".join(turn.sent)

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
                await asyncio.sleep(self.pipeline.cfg.merge_window_sec)
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
                    said = await self._reply(replay)
                    if said:
                        self.pipeline.note_replied(replay, time.time())
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("重放失败")

        self._flush[key] = asyncio.create_task(_later())

    # ── 发 ──
    async def say(self, chat: ChatRef, text: str, quote_id: str = "",
                  at_user: int = 0) -> SendResult:
        out = OutboundMessage(chat=chat, message=Message.from_text(text),
                              quote_id=quote_id, at_user=at_user)
        res = await self.channel.send(out)
        if not res.ok:
            log.warning("发送失败：%s", res.error)
        return res
