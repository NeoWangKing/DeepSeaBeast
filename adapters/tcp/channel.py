"""TCP 文本通道：`telnet 127.0.0.1 4000` 直接聊天（像 telnet BBS）。

协议就是**纯文本行**：
    你输入一行 → 回车发送
    她每说一句 → 一行回复
没有 JSON、没有客户端脚本，telnet / nc / ssh -t 都能用。

安全：只绑 127.0.0.1（不占公网端口）。要从本机连，用
    ssh -N -L 4000:127.0.0.1:4000 agent@服务器        # 一条隧道
    telnet 127.0.0.1 4000
或者一条命令：
    ssh -t agent@服务器 nc 127.0.0.1 4000
"""

from __future__ import annotations

import asyncio
import logging
import time

from adapters import spawn
from core.channel import ChatRef, InboundMessage, Message, OutboundMessage, SendResult, Sender

log = logging.getLogger("tcp")


class TcpChannel:
    name = "tcp"
    supports: set[str] = {"local"}
    abilities: set[str] = {"text"}

    def __init__(self, host: str = "127.0.0.1", port: int = 4000, token: str = "") -> None:
        self.host = host
        self.port = int(port)
        self.token = token or ""
        self._writers: dict[str, asyncio.StreamWriter] = {}
        self._stop: asyncio.Event | None = None
        self._server = None

    def describe(self, chat) -> str:
        return "终端对话（telnet，一对一）"

    # ── 生命周期 ──
    async def start(self, on_message) -> None:
        self._server = await asyncio.start_server(
            lambda r, w: self._handle(r, w, on_message), host=self.host, port=self.port)
        log.info("TCP 通道已就绪：%s:%d（telnet 进去就能聊）", self.host, self.port)
        self._stop = asyncio.Event()
        await self._stop.wait()

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

    # ── 收 ──
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                      on_message) -> None:
        key = ChatRef.local().key
        self._writers[key] = writer              # 多个终端同时连时，回复给最后说话的那个
        pending: list[asyncio.Task] = []         # 后台回合：断开前等它们跑完
        peer = writer.get_extra_info("peername") or ("?", 0)
        log.info("终端接入：%s", peer)
        try:
            writer.write(("已连上，直接打字，回车发送。（Ctrl-C 或关掉窗口退出）\n").encode())
            await writer.drain()
            if self.token:
                writer.write(b"token: ")
                await writer.drain()
                got = (await reader.readline()).decode("utf-8", "ignore").strip()
                if got != self.token:
                    writer.write("token 不对，再见。\n".encode())
                    await writer.drain()
                    return
            while True:
                line = await reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", "ignore").strip()
                if not text:
                    continue
                if text in ("/quit", "/exit", "quit", "exit"):
                    break
                msg = InboundMessage(chat=ChatRef.local(), sender=Sender(0, "终端"),
                                     message=Message.from_text(text), message_id="", at_me=True)
                pending.append(spawn(on_message(msg)))
        except asyncio.CancelledError:
            pass
        except Exception:
            log.debug("终端连接异常（正常断开可忽略）")
        finally:
            if pending:
                # 输入可能已经 EOF（printf | nc），但她的回合还在跑：等它说完再断
                try:
                    await asyncio.wait(pending, timeout=120)
                except Exception:
                    pass
            if self._writers.get(key) is writer:
                self._writers.pop(key, None)
            try:
                writer.close()
            except Exception:
                pass
            log.info("终端断开：%s", peer)

    # ── 发 ──
    async def send(self, out: OutboundMessage) -> SendResult:
        w = self._writers.get(out.chat.key)
        if w is None:
            return SendResult(ok=False, error="没有已连接的终端")
        try:
            for line in str(out.message.text).splitlines() or [""]:
                w.write((line + "\n").encode())
            await w.drain()
            return SendResult(ok=True)
        except Exception as e:
            return SendResult(ok=False, error=repr(e))
