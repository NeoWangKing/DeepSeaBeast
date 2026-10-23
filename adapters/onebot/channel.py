"""OneBot adapter：把 OneBot 事件翻译成 core.channel 的事件，并把发送动作翻回去。"""

from __future__ import annotations

import asyncio
import json
import logging

from core.channel import (ChatRef, InboundMessage, Message, OnMessage, OutboundMessage,
                          Segment, SendResult, Sender)

from .event import Event
from . import faces as qq_faces
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
        self._scope_names: dict[str, str] = {}     # chat.key → 昵称（来自事件）
        self._group_names: dict[str, str] = {}     # chat.key → 群名（查 get_group_info）
        self.bot_id = int(bot_id)
        self._on_message: OnMessage | None = None
        self._stop: asyncio.Event | None = None
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def self_account(self) -> str:
        return str(getattr(self, "bot_id", "") or "")

    async def _fetch_group_name(self, chat) -> None:
        """群名不在 message 事件里，用 get_group_info 查一次；查不到就留着空。"""
        try:
            r = await self._server.call_action("get_group_info",
                                               {"group_id": chat.id}, timeout=10)
            name = str((r.get("data") or {}).get("group_name") or "")
            if name:
                self._group_names[chat.key] = name
                log.info("记下群名：%s → %s", chat.key, name)
        except Exception:
            log.debug("取群名失败（忽略）：%s", chat.key)

    def scope_info(self, chat) -> dict:
        """群/私聊的事实。名字来自事件或 get_group_info，代码里没有任何具体群。"""
        label = "QQ 群" if chat.kind == "group" else ("QQ 私聊" if chat.kind == "private" else "QQ")
        name = self._group_names.get(chat.key) or self._scope_names.get(chat.key, "")
        if chat.kind == "private":
            name = self._scope_names.get(chat.key, "")
        return {"kind": chat.kind, "id": chat.id, "key": chat.key,
                "label": label, "name": name}

    def profile(self) -> dict:
        """QQ 的说话习惯 + 行内可用物（表情清单由核心按群统计后挑选）。"""
        return {
            "style": "这是 QQ。群聊里短句为主、一句话一条，别写长段落；"
                     "不要用 markdown（**粗体**、# 标题、`代码块` 都不渲染）。",
            "inline": {"faces": qq_faces.all_names(), "mention": True, "markdown": False},
            "limits": {"max_chars": 4500},
        }

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
        segs = []
        for x in ev.get_messages():
            data = dict(x.data)
            if x.type == "face":
                fid = str(data.get("id") or "")
                data["name"] = qq_faces.name_of(fid)
                segs.append(Segment(kind="face", text=qq_faces.to_token(fid), data=data))
            else:
                segs.append(Segment(kind=x.type, text=x.text, data=data))
        name = str(ev.sender_name or "")
        if name:
            self._scope_names[chat.key] = name
        if ev.message_type == "group" and chat.key not in self._group_names:
            asyncio.create_task(self._fetch_group_name(chat))
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
                for kind, d in qq_faces.split_text(s.text):
                    raw.append({"type": kind, "data": d})
                continue
            if s.kind == "face":
                fid = str(data.get("id") or "") or qq_faces.id_of(str(data.get("name") or ""))
                if fid:
                    raw.append({"type": "face", "data": {"id": fid}})
                else:
                    raw.append({"type": "text", "data": {"text": s.text}})
                continue
            elif s.kind == "image":
                # QQ 收本地路径要 file:// 前缀；有 url 就直接用
                if data.get("url"):
                    data = {"file": data["url"]}
                elif data.get("path"):
                    data = {"file": "file://" + str(data["path"])}
            raw.append({"type": s.kind, "data": data})
        return raw

    async def _send_file(self, out: OutboundMessage, seg) -> SendResult:
        """文件走 upload_* 接口，不是 send_*_msg。

        注意：这条路**还没在真机上验证过**，所以工具侧把 send_file 标成 experimental，
        默认不给模型。验证通过后再把 status 改成 ready。
        """
        path = str(seg.data.get("path") or "")
        name = str(seg.data.get("name") or "") or (path.rsplit("/", 1)[-1] if path else "")
        if not path:
            return SendResult(ok=False, error="文件路径为空")
        if out.chat.kind == "group":
            action, params = "upload_group_file", {"group_id": out.chat.id,
                                                   "file": path, "name": name}
        else:
            action, params = "upload_private_file", {"user_id": out.chat.id,
                                                     "file": path, "name": name}
        try:
            resp = await self._server.call_action(action, params, timeout=60)
        except Exception as e:
            log.warning("文件上传失败：%r", e)
            return SendResult(ok=False, error=repr(e))
        if int(resp.get("retcode", 0)) != 0:
            return SendResult(ok=False, error=str(resp.get("wording") or resp.get("retcode")))
        return SendResult(ok=True)

    async def send(self, out: OutboundMessage) -> SendResult:
        try:
            # 文件走独立的上传接口，不是 send_*_msg
            for seg in out.message.segments:
                if seg.kind == "file":
                    return await self._send_file(out, seg)
            raw = self._to_raw(out)
            log.debug("发送 → chat=%s %s", out.chat.key, json.dumps(raw, ensure_ascii=False)[:200])
            if out.chat.kind == "group":
                r = await self._server.send_group_msg(out.chat.id, raw)
            else:
                r = await self._server.send_private_msg(out.chat.id, raw)
            log.debug("回包 ← %s", json.dumps(r, ensure_ascii=False)[:200])
            if int(r.get("retcode", 0)) != 0:
                return SendResult(ok=False, error=str(r.get("wording") or r.get("retcode")))
            mid = (r.get("data") or {}).get("message_id")
            return SendResult(ok=True, message_id=str(mid or ""))
        except Exception as e:                     # 发送失败不能把调用方带崩
            log.warning("发送失败：%r", e)
            return SendResult(ok=False, error=repr(e))
