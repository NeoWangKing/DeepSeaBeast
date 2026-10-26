#!/usr/bin/env python3
"""戳一戳自测：收到戳 → 当成被叫到；能戳回去（走 send_poke）。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.onebot.channel import OneBotChannel  # noqa: E402
from adapters.onebot.event import Event  # noqa: E402
from core.channel import ChatRef, Message, OutboundMessage, Segment  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


class FakeServer:
    """假的 OneBot 服务端：记录调用、回固定包。"""

    def __init__(self):
        self.calls = []

    async def call_action(self, action, params=None, timeout=None):
        self.calls.append((action, dict(params or {})))
        return {"status": "ok", "retcode": 0, "data": {}}

    async def send_group_msg(self, gid, msg):
        self.calls.append(("send_group_msg", {"group_id": gid, "message": msg}))
        return {"status": "ok", "retcode": 0, "data": {"message_id": 1}}


async def main() -> int:
    ch = OneBotChannel(bot_id=3237702352)
    got: list = []

    async def on_message(msg):
        got.append(msg)

    ch._on_message = on_message
    fake = FakeServer()
    ch._server = fake

    print("== 收到戳一戳 ==")
    ev = Event(post_type="notice", self_id=3237702352, raw={
        "post_type": "notice", "notice_type": "notify", "sub_type": "poke",
        "group_id": 100000001, "user_id": 200000001, "target_id": 3237702352,
        "sender": {"nickname": "灵其啊"}})
    await ch._handle_notice(ev)
    await asyncio.sleep(0.05)
    ck("收到一条消息", len(got) == 1, str(len(got)))
    if got:
        m = got[0]
        ck("当作被叫到", m.addressed is True and m.addressed_by == "poke",
           "%s/%s" % (m.addressed, m.addressed_by))
        ck("文本占位是「戳了你一下」", "戳了你一下" in m.text, m.text)
        ck("段类型是 poke", any(s.kind == "poke" for s in m.message.segments),
           str([s.kind for s in m.message.segments]))
        ck("会话是那个群", m.chat.kind == "group" and m.chat.id == 100000001, m.chat.key)
        ck("说话人是戳她的人", m.sender.id == 200000001, str(m.sender.id))

    print()
    print("== 戳她以外的人 → 不管 ==")
    got.clear()
    ev2 = Event(post_type="notice", self_id=3237702352, raw={
        "post_type": "notice", "notice_type": "notify", "sub_type": "poke",
        "group_id": 100000001, "user_id": 200000002, "target_id": 999999})
    await ch._handle_notice(ev2)
    await asyncio.sleep(0.05)
    ck("不产生消息", got == [], str(len(got)))

    print()
    print("== 戳回去：走 send_poke ==")
    out = OutboundMessage(chat=ChatRef.group(100000001),
                          message=Message(segments=[Segment("poke", text="[戳一戳]",
                                                            data={"user": 200000001})]))
    r = await ch._send_poke(out, out.message.segments[0])
    ck("发送成功", r.ok is True, str(r.error))
    ck("标为未确认（没有回执）", r.confirmed is False, str(r.confirmed))
    action, params = fake.calls[-1]
    ck("调的是 send_poke", action == "send_poke", action)
    ck("目标对", params.get("user_id") == 200000001, str(params))
    ck("群里带 group_id", params.get("group_id") == 100000001, str(params))

    print()
    print("== 核心工具：只有能戳的通道才看得见 ==")
    from core.tools import DEFAULT_SPECS, schema
    names = [s["function"]["name"] for s in schema(DEFAULT_SPECS, {"text", "poke"})]
    names_no = [s["function"]["name"] for s in schema(DEFAULT_SPECS, {"text"})]
    ck("有 poke 能力 → 有 poke_back", "poke_back" in names, str(names))
    ck("没有 poke 能力 → 没有", "poke_back" not in names_no, str(names_no))

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
