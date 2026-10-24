#!/usr/bin/env python3
"""breamer 通道自测：假服务器按 server.rs 的协议行为回话。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.breamer import BreamerChannel  # noqa: E402
from core.channel import Message, OutboundMessage  # noqa: E402

FAIL: list[str] = []
TOKEN = "test-token"


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


class FakeBreamer:
    """照 server.rs 的行为：先要 token，认证后广播给其它人。"""

    def __init__(self) -> None:
        self.received: list[str] = []
        self.authed = False
        self._server: asyncio.AbstractServer | None = None
        self.port = 0
        self._writers: list[asyncio.StreamWriter] = []

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def _handle(self, reader, writer) -> None:
        self._writers.append(writer)
        writer.write(b"[request] Please enter the token: \n")
        await writer.drain()
        line = (await reader.readline()).decode().strip()
        if line != TOKEN:
            writer.write(b"[warning] Invalid token!\n")
            await writer.drain()
            writer.close()
            return
        self.authed = True
        writer.write(b"[authed] Welcome, you connected seccessfully!\n")
        await writer.drain()
        async for raw in reader:
            text = raw.decode().rstrip("\r\n")
            if text:
                self.received.append(text)
                # 广播给"别人"：这里就回显成 server.rs 的格式，模拟另一个用户说话
                for w in list(self._writers):
                    if w is not writer:
                        w.write(("[message] user7: 收到：" + text + "\n").encode())
                        await w.drain()

    async def push(self, text: str, uid: int = 3) -> None:
        for w in list(self._writers):
            w.write(("[message] user%d: %s\n" % (uid, text)).encode())
            await w.drain()

    async def stop(self) -> None:
        for w in self._writers:
            w.close()
        if self._server:
            self._server.close()


async def main() -> int:
    srv = FakeBreamer()
    port = await srv.start()

    got: list = []

    async def on_message(m):
        got.append(m)

    ch = BreamerChannel(host="127.0.0.1", port=port, token=TOKEN, room_name="测试屋")
    task = asyncio.create_task(ch.start(on_message))
    await asyncio.sleep(0.6)

    print("== 握手 ==")
    ck("服务器确认了 token", srv.authed, str(srv.authed))

    print()
    print("== 收：别人说话 → 核心收到消息 ==")
    await srv.push("你们好啊")
    await asyncio.sleep(0.3)
    ck("收到一条", len(got) == 1, str(len(got)))
    if got:
        m = got[0]
        ck("发送者 id 正确", m.sender.id == 3, str(m.sender.id))
        ck("文本正确", m.text == "你们好啊", m.text)
        ck("会话类型是 room", m.chat.kind == "room", m.chat.kind)
        ck("不是 @（房间没有 @ 机制）", m.addressed is False, str(m.addressed))

    print()
    print("== 发：核心要说话 → 写进连接 ==")
    res = await ch.send(OutboundMessage(chat=got[0].chat if got else ch._chat,
                                        message=Message.from_text("我也在")))
    await asyncio.sleep(0.3)
    ck("发送成功", res.ok is True, str(res.error))
    ck("如实标注未确认", res.confirmed is False, str(res.confirmed))
    ck("服务器收到了正文", srv.received and srv.received[0] == "我也在", str(srv.received))

    print()
    print("== 多行压成一行（服务器按行广播）==")
    await ch.send(OutboundMessage(chat=ch._chat, message=Message.from_text("第一行\n第二行")))
    await asyncio.sleep(0.3)
    ck("没有把一条拆成两条", len(srv.received) == 2, str(srv.received))
    ck("换行被替换", "\n" not in srv.received[1], srv.received[1])

    print()
    print("== 通道能力 ==")
    ck("只有文字能力", ch.abilities == {"text"}, str(ch.abilities))
    ck("profile 说明不能发表情/图片", "没有图片" in ch.profile()["style"], ch.profile()["style"][:30])

    task.cancel()
    await ch.stop()
    await srv.stop()
    await asyncio.sleep(0.2)

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
