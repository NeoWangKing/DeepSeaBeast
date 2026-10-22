#!/usr/bin/env python3
"""多通道自测：路由、能力隔离、同时收发。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.channel import ChatRef, OutboundMessage, Message, SendResult  # noqa: E402
from core.channel_manager import ChannelManager  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


class FakeChannel:
    def __init__(self, name, supports, abilities):
        self.name = name
        self.supports = set(supports)
        self.abilities = set(abilities)
        self.sent: list[str] = []

    async def start(self, on_message):
        await asyncio.Event().wait()

    async def stop(self):
        pass

    async def send(self, out):
        self.sent.append(out.message.text)
        return SendResult(ok=True, message_id="x")


async def main() -> int:
    mgr = ChannelManager()
    cli = FakeChannel("cli", {"local"}, {"text"})
    ob = FakeChannel("onebot", {"group", "private"}, {"text", "at", "face"})
    mgr.add(cli)
    mgr.add(ob)

    print("== 路由 ==")
    ck("local → cli", mgr.route(ChatRef.local()) is cli)
    ck("group → onebot", mgr.route(ChatRef.group(1)) is ob)
    ck("private → onebot", mgr.route(ChatRef.private(1)) is ob)

    print()
    print("== 能力按会话隔离 ==")
    ck("cli 只有 text", mgr.abilities_for(ChatRef.local()) == {"text"})
    ck("群里有 face", "face" in mgr.abilities_for(ChatRef.group(1)))

    print()
    print("== 同时发送 ==")
    await mgr.send(OutboundMessage(chat=ChatRef.local(), message=Message.from_text("claude 端")))
    await mgr.send(OutboundMessage(chat=ChatRef.group(1), message=Message.from_text("qq 端")))
    ck("CLI 收到自己的", cli.sent == ["claude 端"], str(cli.sent))
    ck("QQ 收到自己的", ob.sent == ["qq 端"], str(ob.sent))

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
