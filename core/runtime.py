"""运行时：收消息 → 管线判断 → （M2：占位回复 / M3：LLM）→ 交给 channel 发。

连发合并的"延后重放"也在这里：管线返回 defer 时，过 merge_window 秒把攒下的
文本当成一条新消息再跑一次（这次跳过 merge 段）。
"""

from __future__ import annotations

import asyncio
import logging
import time

from .channel import ChatRef, InboundMessage, Message, OutboundMessage, SendResult
from .llm import LLMConfig, chat
from .pipeline import GateConfig, Pipeline
from .prompt import build_messages

log = logging.getLogger("runtime")


class Runtime:
    def __init__(self, channel, gate: GateConfig | None = None,
                 owner_ids: list[int] | None = None, llm: LLMConfig | None = None,
                 reply_fn=None) -> None:
        self.channel = channel
        self.owner_ids = set(owner_ids or [])
        self.pipeline = Pipeline(gate or GateConfig())
        self.llm = llm or LLMConfig()
        # 测试可以注入假回复；不注入就走真模型
        self.reply_fn = reply_fn or self._llm_reply
        self._flush: dict[str, asyncio.Task] = {}

    # ── 收 ──
    async def on_message(self, msg: InboundMessage) -> None:
        now = time.time()
        out = self.pipeline.run(msg, now)
        log.info("判断 [%s] %r → %s（%s%s）", msg.chat.key, msg.text[:30],
                 out.kind, out.stage or "-", ("：" + out.reason) if out.reason else "")
        if out.kind == "defer":
            self._schedule_replay(msg, out.text, now)
            return
        if out.kind != "reply":
            return
        text = await self._reply(msg)
        if not text:
            return
        res = await self.say(msg.chat, text, quote_id=msg.message_id)
        if res.ok:
            self.pipeline.note_replied(msg, time.time())
            log.info("已回复 [%s] %r", msg.chat.key, text[:40])

    # ── 回复：默认走真模型，失败就静默（不编话）──
    async def _reply(self, msg: InboundMessage) -> str:
        try:
            return await self.reply_fn(msg)
        except Exception:
            log.exception("生成回复失败，这条不回")
            return ""

    async def _llm_reply(self, msg: InboundMessage) -> str:
        state = self.pipeline.state_for(msg.chat.key)
        messages = build_messages(msg, list(state.recent))
        text = await asyncio.to_thread(chat, messages, self.llm, 300)
        text = " ".join(str(text or "").split())
        if not text:
            return ""
        state.recent.append(("我", text[:120], 0, time.time()))
        state.recent.append((msg.sender.name or "对方", msg.text[:120],
                             msg.sender.id, time.time()))
        return text[:400]

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
                    return                    # 已经被后来的消息合并掉了
                replay = InboundMessage(
                    chat=msg.chat, sender=msg.sender,
                    message=Message.from_text(state.merge_buf),
                    message_id=msg.message_id, at_me=msg.at_me, raw=msg.raw)
                state.merge_buf = ""
                out = self.pipeline.run(replay, time.time(), skip=("merge",))
                tag = ("：" + out.reason) if out.reason else ""
                log.info("重放合并 [%s] %r → %s（%s%s）",
                         key, replay.text[:30], out.kind, out.stage or "-", tag)
                if out.kind == "reply":
                    text = await self._reply(replay)
                    res = await self.say(replay.chat, text, quote_id=replay.message_id)
                    if res.ok:
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
