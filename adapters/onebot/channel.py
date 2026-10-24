"""OneBot adapter：把 OneBot 事件翻译成 core.channel 的事件，并把发送动作翻回去。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os

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
                 bot_id: int = 0, verify_send: bool = True,
                 verify_timeout_ms: int = 3000) -> None:
        self._server = OneBotServer(host, port, allow_self_ids)
        self._owner_ids = set(owner_ids or [])
        self._scope_names: dict[str, str] = {}     # chat.key → 昵称（来自事件）
        self._group_names: dict[str, str] = {}     # chat.key → 群名（查 get_group_info）
        self._bot_name: str = ""                   # 机器人昵称（连接时查 get_login_info）
        self.bot_id = int(bot_id)
        self._on_message: OnMessage | None = None
        self._stop: asyncio.Event | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        # 发送后回读核对：retcode=0 只说明"接口收下了"，不说明"消息真的存在"
        self._verify = bool(verify_send)
        self._verify_timeout = max(500, int(verify_timeout_ms)) / 1000.0

    @property
    def self_account(self) -> str:
        return str(getattr(self, "bot_id", "") or "")

    def identity(self, chat) -> dict:
        """在这个群里她叫什么：群名片 > 昵称 > QQ 号。"""
        name = self._scope_names.get(chat.key) or self._bot_name
        aliases: list[str] = []
        for x in (self._bot_name, name, str(self.bot_id or "")):
            if x and x not in aliases:
                aliases.append(x)
        return {"name": name or str(self.bot_id or ""),
                "aliases": aliases, "account": str(self.bot_id or "")}

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
        addressed = bool(self.bot_id) and (at_by_seg or at_by_text)
        how = "at" if at_by_seg else ("name" if at_by_text else "")
        if at_by_text:
            log.info("没有 at 段，但文字里认出了 @ 她：%r", text[:40])
        if not addressed and "@" in text:
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
            addressed=addressed, addressed_by=how,
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
                    raw.append({"type": "image", "data": _image_file(f)})
                continue
            if s.kind == "face":
                fid = str(data.get("id") or "") or qq_faces.id_of(str(data.get("name") or ""))
                if fid:
                    raw.append({"type": "face", "data": {"id": fid}})
                else:
                    raw.append({"type": "text", "data": {"text": s.text}})
                continue
            elif s.kind == "image":
                f = data.get("url") or data.get("path") or data.get("file")
                data = _image_file(f) if f else data
            raw.append({"type": s.kind, "data": data})
        return raw

    async def _verify_sent(self, message_id, chat) -> tuple[bool, str, bool]:
        """发送后回读：确认这条消息真的在服务端存在。

        retcode=0 只代表"发消息接口收下了"；回读能让"发出去但没落地"这类
        静默失败暴露出来（实测 get_msg 对不存在的 id 返回 100 message not found）。

        协议不支持/接口不可用时**自动关掉核对**，只警告一次，不影响正常发送。
        """
        last_err = ""
        for attempt in (1, 2):
            try:
                r = await self._server.call_action(
                    "get_msg", {"message_id": message_id}, timeout=self._verify_timeout)
            except Exception as e:
                last_err = "回读异常：%r" % (e,)
            else:
                code = int(r.get("retcode") or 0)
                if code == 0:
                    data = r.get("data") or {}
                    if chat.kind == "group":
                        gid = data.get("group_id")
                        if gid is not None and int(gid) != int(chat.id):
                            return False, "回读到的是别的群的消息（%s ≠ %s）" % (gid, chat.id), True
                    return True, "", True
                if code in (100, 1404) and "not found" in str(r.get("wording") or "").lower():
                    last_err = "服务端查不到这条消息（%s）" % (r.get("wording") or code)
                elif code == 1404 or "unknown" in str(r.get("wording") or "").lower():
                    # 这个实现不支持 get_msg：别让核对变成误报，直接关掉
                    self._verify = False
                    log.warning("对方不支持 get_msg，已关闭发送回执核对")
                    return True, "", False
                else:
                    last_err = "回读失败：%s %s" % (code, r.get("wording") or "")
            await asyncio.sleep(0.2)      # 服务端可能晚一点才可查
        return False, last_err or "回读失败", True

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
            return SendResult(ok=False, error=str(resp.get("wording") or resp.get("retcode")),
                              retryable=False)
        return SendResult(ok=True, confirmed=False, error="文件上传没有回执，无法确认")

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
                code = int(r.get("retcode") or 0)
                # 1xx 多为参数/目标不存在这类硬错误，重试没意义；其它当瞬时故障
                permanent = 100 <= code < 200
                return SendResult(ok=False, error=str(r.get("wording") or code),
                                  retryable=not permanent)
            mid = (r.get("data") or {}).get("message_id")
            if mid is None:
                # 没有 message_id 就没法回读，只能说"接口收下了"
                return SendResult(ok=True, confirmed=False,
                                  error="没有 message_id，无法回读确认")
            if self._verify:
                ok, err, checked = await self._verify_sent(mid, out.chat)
                if not ok:
                    return SendResult(ok=False, error=err, retryable=True)   # 回读不到，可重试
                return SendResult(ok=True, message_id=str(mid), confirmed=checked)
            return SendResult(ok=True, message_id=str(mid), confirmed=False)
        except Exception as e:                     # 发送失败不能把调用方带崩
            log.warning("发送失败：%r", e)
            return SendResult(ok=False, error=repr(e), retryable=True)   # 连接类问题，值得重试


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


MAX_IMAGE_BYTES = 4 * 1024 * 1024     # 再大就别 base64 了，直接报错比塞爆连接好


def _image_file(src: str) -> dict:
    """把"本地路径 / URL"变成 OneBot 能吃的 file 字段。

    本地文件**必须用 base64**：SnowLuma 以另一个用户运行，读不到我们的数据目录
    （实测 file:// 和绝对路径都报 EACCES，base64 才是通的）。
    """
    s = str(src or "")
    if not s:
        return {"file": ""}
    if s.startswith(("http://", "https://", "base64://")):
        return {"file": s}
    try:
        size = os.path.getsize(s)
    except OSError as e:
        log.warning("图片读不到：%s（%r）", s, e)
        return {"file": s}
    if size > MAX_IMAGE_BYTES:
        log.warning("图片太大不发：%s（%.1f MB）", s, size / 1048576)
        return {"file": s}
    try:
        with open(s, "rb") as f:
            return {"file": "base64://" + base64.b64encode(f.read()).decode()}
    except OSError as e:
        log.warning("图片读取失败：%s（%r）", s, e)
        return {"file": s}
