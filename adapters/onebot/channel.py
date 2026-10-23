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
    abilities: set[str] = {"text", "at", "quote", "image", "file", "face",
                           "sticker", "poke", "voice"}
    supports: set[str] = {"group", "private"}

    def __init__(self, host: str = "127.0.0.1", port: int = 6200,
                 allow_self_ids: list[int] | None = None, owner_ids: list[int] | None = None,
                 bot_id: int = 0) -> None:
        self._server = OneBotServer(host, port, allow_self_ids)
        self._owner_ids = set(owner_ids or [])
        self._scope_names: dict[str, str] = {}     # chat.key → 昵称（来自事件）
        self._group_names: dict[str, str] = {}     # chat.key → 群名（查 get_group_info）
        self._bot_name: str = ""                   # 机器人昵称（连接时查 get_login_info）
        self.bot_id = int(bot_id)
        self._on_message: OnMessage | None = None
        self._stop: asyncio.Event | None = None
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def self_account(self) -> str:
        return str(getattr(self, "bot_id", "") or "")

    def _text_mentions_me(self, text: str) -> bool:
        """有些客户端/转发会把 @ 变成纯文本，没有 at 段。

        这种情况按文本认：出现 "@机器人昵称" 或 "@机器人QQ" 就算叫她。
        """
        t = str(text or "")
        if "@" not in t:
            return False
        if self._bot_name and ("@" + self._bot_name) in t:
            return True
        if self.bot_id and ("@%d" % int(self.bot_id)) in t:
            return True
        return False

    async def _fetch_bot_name(self) -> None:
        """查一次机器人自己的昵称：有些客户端 @ 只带名字不带 qq。"""
        for _ in range(30):
            try:
                if self._server.connected:
                    r = await self._server.call_action("get_login_info", {}, timeout=8)
                    self._bot_name = str((r.get("data") or {}).get("nickname") or "")
                    if self._bot_name:
                        log.info("机器人昵称：%s", self._bot_name)
                        return
            except Exception:
                pass
            await asyncio.sleep(2)

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
        asyncio.create_task(self._fetch_bot_name())
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
            if x.type == "image" and _is_sticker_segment(data):
                # 群里发的表情包 → 核心侧是 sticker（不是图片）
                desc = str(data.get("summary") or "").strip("[]")
                segs.append(Segment(kind="sticker",
                                    text=qq_faces.sticker_token(desc or "贴图"),
                                    data={"name": desc,
                                          "url": str(data.get("url") or ""),
                                          "file": str(data.get("file") or "")}))
                continue
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
        text = str(ev.message.text or "")
        at_by_seg = bool(self.bot_id) and ev.is_at(self.bot_id, self._bot_name)
        at_by_text = (not at_by_seg) and self._text_mentions_me(text)
        at_me = bool(self.bot_id) and (at_by_seg or at_by_text)
        if at_by_text:
            log.info("没有 at 段，但文字里认出了 @ 她：%r", text[:40])
        if not at_me and "@" in text:
            # 被 @ 了却没认出来 —— 把原始报文打出来，看客户端到底给了什么形状
            log.info("疑似 @ 没认出来：at段=%s raw=%s",
                     json.dumps([{"type": s.type, "data": s.data}
                                 for s in ev.message.segments if s.type == "at"],
                                ensure_ascii=False)[:120],
                     json.dumps(ev.raw, ensure_ascii=False)[:400])
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
            if s.kind == "sticker":
                st = dict(s.data)
                f = st.get("path") or st.get("url") or st.get("file")
                if f:
                    raw.append({"type": "image", "data": {"file": f if "://" in str(f)
                                                          else "file://" + str(f)}})
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


def _is_sticker_segment(data: dict) -> bool:
    """OneBot 的图片段里，哪些其实是"表情包"。

    常见判据：sub_type == 1（QQ 的"动画表情"），或 summary 里带「动画表情」字样。
    """
    try:
        if str(data.get("sub_type") or "") == "1":
            return True
    except Exception:
        pass
    summary = str(data.get("summary") or "")
    return "动画表情" in summary or "表情" in summary
