"""运行时：收到消息 → （后续接管线）→ 决定回什么 → 交给 channel 发出去。

阶段 M1：先把"@ 她 → 回一句"跑通，证明双向链路成立。
管线（激活/白名单/合并/打分/decide）在 M2 接进来，替换 `_decide`。
"""

from __future__ import annotations

import logging

from .channel import ChatRef, InboundMessage, Message, OutboundMessage, SendResult

log = logging.getLogger("runtime")


class Runtime:
    def __init__(self, channel, owner_ids: list[int] | None = None) -> None:
        self.channel = channel
        self.owner_ids = set(owner_ids or [])

    # ── 收 ──
    async def on_message(self, msg: InboundMessage) -> None:
        log.info("收到 [%s] %s(%s): %r%s", msg.chat.key, msg.sender.name or "?",
                 msg.sender.id, msg.text[:60], "（在叫她）" if msg.at_me else "")
        decision = self._decide(msg)
        if decision is None:
            return
        text, extra = decision
        await self.say(msg.chat, text, **extra)

    # ── 决策（M1 的占位实现，M2 换成真管线）──
    def _decide(self, msg: InboundMessage) -> tuple[str, dict] | None:
        if msg.chat.kind == "local":
            return "收到：%s" % msg.text, {}
        if msg.at_me:
            return "在的，你说的是「%s」" % msg.text[:40], {"quote_id": msg.message_id}
        return None

    # ── 发 ──
    async def say(self, chat: ChatRef, text: str, quote_id: str = "",
                  at_user: int = 0) -> SendResult:
        out = OutboundMessage(chat=chat, message=Message.from_text(text),
                              quote_id=quote_id, at_user=at_user)
        res = await self.channel.send(out)
        if not res.ok:
            log.warning("发送失败：%s", res.error)
        return res
