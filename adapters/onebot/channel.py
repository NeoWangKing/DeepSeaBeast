"""OneBot adapter：把 OneBot 事件翻译成 core.channel 的事件，并把发送动作翻回去。"""

from __future__ import annotations

import asyncio
import logging

from core.channel import (ChatRef, InboundMessage, Message, OnMessage, OutboundMessage,
                          Segment, SendResult, Sender)

from .event import Event
from .server import OneBotServer

log = logging.getLogger("onebot")


class OneBotChannel:
    name = "onebot"
    # SnowLuma 这边支持得比较全；真正发的时候再按能力挑
    abilities: set[str] = {"text", "at", "quote", "image", "file", "face", "poke", "voice"}
    supports: set[str] = {"group", "private"}

    def __init__(self, host: str = "127.0.0.1", port: int = 6200,
                 allow_self_ids: list[int] | None = None, owner_ids: list[int] | None = None,
                 bot_id: int = 0) -> None:
        self._server = OneBotServer(host, port, allow_self_ids)
        self._owner_ids = set(owner_ids or [])
        self.bot_id = int(bot_id)
        self._on_message: OnMessage | None = None
        self._stop: asyncio.Event | None = None
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def self_account(self) -> str:
        return str(getattr(self, "bot_id", "") or "")

    def describe(self, chat) -> str:
        if chat.kind == "group":
            return "QQ 群 %d" % chat.id
        if chat.kind == "private":
            return "QQ 私聊"
        return "QQ"

    # ── 生命周期 ──
    async def start(self, on_message: OnMessage) -> None:
        self._on_message = on_message
        self._server.on_event(self._handle)
        await self._server.start()
        self._stop = asyncio.Event()
        await self._stop.wait()          # 一直跑到 stop() 被调用

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        await self._server.stop()

    @property
    def connected(self) -> bool:
        return self._server.connected

    # ── 收：OneBot Event → InboundMessage ──
    async def _handle(self, ev: Event, _server: OneBotServer) -> None:
        if ev.post_type != "message" or self._on_message is None:
            return
        if ev.message_type == "group":
            chat = ChatRef.group(ev.group_id)
        else:
            chat = ChatRef.private(ev.user_id)
        # adapter 负责把 OneBot 段"归一化"：核心只读 kind + text，data 留作通道私有
        segs = [Segment(kind=x.type, text=x.text, data=dict(x.data))
                for x in ev.get_messages()]
        at_me = bool(self.bot_id) and ev.is_at(self.bot_id)
        msg = InboundMessage(
            chat=chat,
            sender=Sender(id=ev.user_id, name=ev.sender_name,
                          is_owner=ev.user_id in self._owner_ids),
            message=Message(segments=segs, raw=ev.raw),
            message_id=str(ev.message_id),
            at_me=at_me,
            raw=ev.raw,
        )
        # 同一个会话串行处理（避免两条消息同时跑管线），不同会话互不影响
        lock = self._locks.setdefault(chat.key, asyncio.Lock())
        async with lock:
            await self._on_message(msg)

    # ── 发：OutboundMessage → OneBot 动作 ──
    @staticmethod
    def _to_raw(out: OutboundMessage) -> list[dict]:
        segs = out.message.segments
        raw: list[dict] = []
        if out.quote_id and "quote" in OneBotChannel.abilities:
            raw.append({"type": "reply", "data": {"id": str(out.quote_id)}})
        if out.at_user:
            raw.append({"type": "at", "data": {"qq": str(out.at_user)}})
        for s in segs:
            # 核心给的段只保证 kind/text；发出去时按 OneBot 格式还原
            data = dict(s.data) if s.data else {}
            if s.kind == "text":
                data = {"text": s.text}
            raw.append({"type": s.kind, "data": data})
        return raw

    async def send(self, out: OutboundMessage) -> SendResult:
        try:
            raw = self._to_raw(out)
            if out.chat.kind == "group":
                r = await self._server.send_group_msg(out.chat.id, raw)
            else:
                r = await self._server.send_private_msg(out.chat.id, raw)
            if int(r.get("retcode", 0)) != 0:
                return SendResult(ok=False, error=str(r.get("wording") or r.get("retcode")))
            mid = (r.get("data") or {}).get("message_id")
            return SendResult(ok=True, message_id=str(mid or ""))
        except Exception as e:                     # 发送失败不能把调用方带崩
            log.warning("发送失败：%r", e)
            return SendResult(ok=False, error=repr(e))
