"""Unix socket 通道：SSH 上去就能和**运行中的核心**聊天。

协议（每行一个 JSON）：
    客户端 → 核心  {"text": "在吗"}
    核心 → 客户端  {"text": "在呢"}      # 一条消息一行，连发就是多行
断线即结束；不影响 QQ 那条通道。

作用域用 local（和 CLI 一样），所以工具集只有 text —— 终端里她不会去发表情包。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from adapters import spawn
from core.channel import ChatRef, InboundMessage, Message, OutboundMessage, SendResult, Sender

log = logging.getLogger("socket")


class SocketChannel:
    name = "socket"
    supports: set[str] = {"local"}
    abilities: set[str] = {"text"}

    def __init__(self, path: str = "/tmp/deepsea.sock", mode: int = 0o666) -> None:
        self.path = path
        self.mode = mode
        self._writers: dict[str, asyncio.StreamWriter] = {}
        self._idle: dict[str, asyncio.Event] = {}    # 会话空闲信号（EOF 后等它再关）
        self._stop: asyncio.Event | None = None
        self._server = None

    def scope_info(self, chat) -> dict:
        return {"kind": chat.kind, "id": chat.id, "key": chat.key,
                "label": "脚本接口", "name": ""}

    def profile(self) -> dict:
        return {"style": "这是终端（脚本接入）。纯文本，别用表情符号和 markdown。",
                "inline": {"faces": [], "mention": False, "markdown": False},
                "limits": {"max_chars": 8000}}

    def describe(self, chat) -> str:
        return "终端对话（socket，一对一）"

    # ── 生命周期 ──
    async def start(self, on_message) -> None:
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        self._server = await asyncio.start_unix_server(
            lambda r, w: self._handle(r, w, on_message), path=self.path)
        try:
            os.chmod(self.path, self.mode)
        except Exception:
            pass
        log.info("socket 通道已就绪：%s（SSH 上去可直接聊天）", self.path)
        self._stop = asyncio.Event()
        await self._stop.wait()

    def idle(self, chat) -> None:
        """运行时说"这个会话暂时没活了" → 放掉等待中的连接。"""
        ev = self._idle.get(chat.key)
        if ev is not None:
            ev.set()

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        for w in list(self._writers.values()):
            try:
                w.close()
            except Exception:
                pass
        self._writers.clear()
        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=3)
            except Exception:
                pass
        try:
            os.unlink(self.path)
        except Exception:
            pass

    # ── 收 ──
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                      on_message) -> None:
        key = ChatRef.local().key
        self._writers[key] = writer
        pending: list[asyncio.Task] = []         # 后台回合：断开前等它们跑完
        idle = asyncio.Event()
        self._idle[key] = idle
        log.info("终端已接入")
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    data = json.loads(line.decode("utf-8", "ignore"))
                except Exception:
                    continue
                text = str(data.get("text") or "").strip()
                if not text:
                    continue
                msg = InboundMessage(chat=ChatRef.local(), sender=Sender(0, "终端"),
                                     message=Message.from_text(text), message_id="", at_me=True)
                pending.append(spawn(on_message(msg)))
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("终端连接异常")
        finally:
            self._writers.pop(key, None)
            try:
                writer.close()
            except Exception:
                pass
            log.info("终端断开")

    # ── 发 ──
    async def send(self, out: OutboundMessage) -> SendResult:
        for seg in out.message.segments:
            if seg.kind in ("file", "image", "sticker", "face", "poke", "voice"):
                return SendResult(ok=False, error="这个通道发不了 %s" % seg.kind)
        w = self._writers.get(out.chat.key)
        if w is None:
            return SendResult(ok=False, error="没有已连接的终端")
        try:
            w.write((json.dumps({"text": out.message.text}, ensure_ascii=False) + "\n").encode())
            await w.drain()
            return SendResult(ok=True, confirmed=False, error="终端没有回执，无法确认")
        except Exception as e:
            return SendResult(ok=False, error=repr(e))
