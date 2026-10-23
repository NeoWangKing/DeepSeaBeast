"""命令行 adapter：stdin 收、stdout 发。

用途：本地开发时不用 QQ/SnowLuma 就能测核心逻辑；也是"核心不认识 QQ"的活证据。
"""

from __future__ import annotations

import asyncio
import logging

from adapters import spawn
from core.channel import (ChatRef, InboundMessage, Message, OnMessage, OutboundMessage,
                          SendResult, Sender)

log = logging.getLogger("cli")


class CliChannel:
    name = "cli"
    # CLI 只有文本；at/quote/image/file/face/poke 都声明不了 → 相关工具会被核心隐藏
    abilities: set[str] = {"text"}
    supports: set[str] = {"local"}

    def __init__(self, user_name: str = "本地用户", owner_id: int = 0) -> None:
        self.user_name = user_name
        self._owner_id = owner_id
        self._on_message: OnMessage | None = None
        self._stop = asyncio.Event()

    def scope_info(self, chat) -> dict:
        return {"kind": chat.kind, "id": chat.id, "key": chat.key,
                "label": "命令行", "name": ""}

    def profile(self) -> dict:
        return {"style": "这是命令行。纯文本，别用表情符号和 markdown，可以把话说完整。",
                "inline": {"faces": [], "mention": False, "markdown": False},
                "limits": {"max_chars": 8000}}

    def describe(self, chat) -> str:
        return "命令行对话（一对一）"

    async def start(self, on_message: OnMessage) -> None:
        self._on_message = on_message
        log.info("CLI 模式：直接输入文字聊天（Ctrl-D 或 /quit 退出）")
        pending: list[asyncio.Task] = []      # 后台回合：退出前等它们说完
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            try:
                line = await loop.run_in_executor(None, input, "你> ")
            except EOFError:
                break
            text = line.strip()
            if not text:
                continue
            if text in ("/quit", "/exit"):
                break
            msg = InboundMessage(
                chat=ChatRef.local(),
                sender=Sender(id=self._owner_id, name=self.user_name, is_owner=True),
                message=Message.from_text(text),
                message_id="",
                at_me=True,          # 命令行里说话就是冲她说的
            )
            if self._on_message is not None:
                pending.append(spawn(self._on_message(msg)))
        if pending:
            try:
                await asyncio.wait(pending, timeout=120)
            except Exception:
                pass
        self._stop.set()

    async def send(self, out: OutboundMessage) -> SendResult:
        for seg in out.message.segments:
            if seg.kind in ("file", "image", "sticker", "face", "poke", "voice"):
                return SendResult(ok=False, error="这个通道发不了 %s" % seg.kind)
        print("鱼> %s" % out.message.text)
        return SendResult(ok=True)

    async def stop(self) -> None:
        self._stop.set()
