"""breamer 通道：连自己搭的 TCP 聊天室（server.rs），像普通用户一样收发。

协议（对方 server.rs 的约定，逐条对齐）：
  连上后服务器先发      `[request] Please enter the token: `
  客户端发一行口令      `<token>\\n`
  口令正确              `[authed] Welcome, ...`
  之后每行文本广播给**其它**已登录用户：`[message] user<id>: <text>`
  限速                 每人 1 条/秒，超了记 strike，10 次封 IP 10 分钟
  行尾                  `\\n`（`\\r` 会被服务器自己去掉）

所以这里只做翻译：`[message] user3: 你好` → 核心的 InboundMessage；
核心要发的文字 → 一行 `\\n` 结束的文本。
"""

from __future__ import annotations

import asyncio
import logging
import re

from core.channel import ChatRef, InboundMessage, Message, OutboundMessage, SendResult, Sender

log = logging.getLogger("breamer")

MSG_RE = re.compile(r"^\[message\]\s+user(\d+):\s?(.*)$", re.S)


class BreamerChannel:
    name = "breamer"
    abilities: set[str] = {"text"}      # 纯文本天花板：没有图片/表情/@/引用
    supports: set[str] = {"room"}

    def __init__(self, host: str = "127.0.0.1", port: int = 6969, token: str = "",
                 room_key: str = "breamer", room_name: str = "聊天室",
                 self_name: str = "") -> None:
        self.host = str(host)
        self.port = int(port)
        self.token = str(token or "")
        self.room_key = room_key
        self.room_name = room_name
        self.self_name = self_name
        self._chat = ChatRef.room(0, room_key)
        self._writer: asyncio.StreamWriter | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self.self_account = self_name

    # ── 通道契约 ────────────────────────────────────────
    def describe(self, chat) -> str:
        return "一个自建的 TCP 聊天室（%s）" % self.room_name

    def scope_info(self, chat) -> dict:
        return {"kind": "room", "id": 0, "key": self.room_key,
                "label": "聊天室", "name": self.room_name}

    def profile(self) -> dict:
        return {
            "style": "这是一个纯文本聊天室：只能用文字，没有图片、表情包、@、引用。"
                     "发出去的消息会原样显示给房间里的所有人。",
            "inline": {"faces": [], "mention": False, "markdown": False},
            "limits": {"max_chars": 2000},
        }

    async def start(self, on_message) -> None:
        self._stop.clear()
        backoff = 2.0
        while not self._stop.is_set():
            try:
                await self._run_once(on_message)
                backoff = 2.0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("breamer 连接断开：%r，%.0fs 后重连", e, backoff)
            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2)

    async def stop(self) -> None:
        self._stop.set()
        w = self._writer
        if w is not None:
            try:
                w.close()
            except Exception:
                pass

    async def send(self, out: OutboundMessage) -> SendResult:
        text = (out.message.text or "").strip()
        if not text:
            return SendResult(ok=False, error="空消息不发", retryable=False)
        w = self._writer
        if w is None:
            return SendResult(ok=False, error="还没连上聊天室", retryable=True)
        # 服务器自己会拼 "[message] user<id>: "，这里只发正文；多行压成一行
        line = text.replace("\r", " ").replace("\n", " / ")
        async with self._lock:
            try:
                w.write((line + "\n").encode("utf-8"))
                await w.drain()
            except Exception as e:
                return SendResult(ok=False, error="写入失败：%r" % (e,), retryable=True)
        # 服务器只广播给别人，不会回显给我们，所以拿不到消息 id
        return SendResult(ok=True, confirmed=False, error="聊天室没有回执，无法确认")

    # ── 内部 ────────────────────────────────────────────
    async def _run_once(self, on_message) -> None:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        self._writer = writer
        try:
            # ① 等服务器的 token 提示
            prompt = await asyncio.wait_for(reader.readline(), timeout=10)
            log.info("breamer 提示：%s", prompt.decode("utf-8", "replace").strip()[:60])
            # ② 交口令
            writer.write((self.token + "\n").encode("utf-8"))
            await writer.drain()
            # ③ 等鉴权结果
            resp = (await asyncio.wait_for(reader.readline(), timeout=10)).decode("utf-8", "replace").strip()
            if not resp.startswith("[authed]"):
                raise RuntimeError("鉴权失败：%s" % resp[:60])
            log.info("breamer 已进入房间：%s:%d", self.host, self.port)
            # ④ 收消息
            while not self._stop.is_set():
                raw = await reader.readline()
                if not raw:
                    raise ConnectionError("服务器关闭了连接")
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                await self._dispatch(line, on_message)
        finally:
            self._writer = None
            try:
                writer.close()
            except Exception:
                pass

    async def _dispatch(self, line: str, on_message) -> None:
        m = MSG_RE.match(line)
        if not m:
            if line.strip():
                log.debug("breamer 其它行：%s", line[:80])
            return
        uid, text = int(m.group(1)), m.group(2)
        if not text.strip():
            return
        msg = InboundMessage(chat=self._chat, sender=Sender(uid, "user%d" % uid),
                             message=Message.from_text(text), message_id="",
                             at_me=False)   # 房间没有 @ 机制，门控按 room 规则处理
        await on_message(msg)
