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
from core.channel import ChatRef, Message, OutboundMessage, Segment, Sender  # noqa: E402

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
    print("== 戳别人 → 她看见了，不标被叫到，但记住焦点 ==")
    got.clear()
    ev2 = Event(post_type="notice", self_id=3237702352, raw={
        "post_type": "notice", "notice_type": "notify", "sub_type": "poke",
        "group_id": 100000001, "user_id": 200000002, "target_id": 300000003,
        "target_name": "灵其啊"})
    await ch._handle_notice(ev2)
    await asyncio.sleep(0.05)
    ck("产生一条消息", len(got) == 1, str(len(got)))
    if got:
        m2 = got[0]
        ck("不算被叫到", m2.addressed is False, str(m2.addressed))
        ck("文本说明是谁戳了谁", "戳了「灵其啊」一下" in m2.text, m2.text)
        ck("焦点是被戳的那个人",
           m2.focus is not None and m2.focus.id == 300000003,
           str(getattr(m2.focus, "id", None)))

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
    print("== 跟着戳同一个：poke_same 用焦点对象 ==")
    from core.channel import InboundMessage
    from core.tools import Turn
    got.clear()
    ev3 = Event(post_type="notice", self_id=3237702352, raw={
        "post_type": "notice", "notice_type": "notify", "sub_type": "poke",
        "group_id": 100000001, "user_id": 200000002, "target_id": 300000003,
        "target_name": "灵其啊"})
    await ch._handle_notice(ev3)
    await asyncio.sleep(0.05)
    seen = got[0]
    fake.calls.clear()
    turn = Turn(ch, seen, None, abilities={"text", "poke"})
    out = await turn.dispatch("poke_same", {})
    ck("工具说戳到了", "戳了" in out, out)
    action, params = fake.calls[-1]
    ck("戳的是被戳的那个人（不是戳人的那个）",
       action == "send_poke" and params.get("user_id") == 300000003, str(params))

    print()
    print("== 没有焦点时 poke_same 不乱戳 ==")
    plain = InboundMessage(chat=ChatRef.group(100000001),
                           sender=Sender(id=200000002, name="a"),
                           message=Message.from_text("随便聊聊"), message_id="1")
    turn2 = Turn(ch, plain, None, abilities={"text", "poke"})
    out2 = await turn2.dispatch("poke_same", {})
    ck("拒绝戳", "没有可以跟着戳" in out2, out2)

    print()
    print("== 自己戳出去的回传 → 不再触发新回合（防无限循环）==")
    got.clear()
    ev_self = Event(post_type="notice", self_id=3237702352, raw={
        "post_type": "notice", "notice_type": "notify", "sub_type": "poke",
        "group_id": 100000001, "user_id": 3237702352, "target_id": 200000001})
    await ch._handle_notice(ev_self)
    await asyncio.sleep(0.05)
    ck("不产生消息", got == [], str(len(got)))

    print()
    print("== 一轮最多戳一次 ==")
    turn3 = Turn(ch, plain, None, abilities={"text", "poke"})
    fake.calls.clear()
    await turn3.dispatch("poke_back", {"user": 200000001})
    out3 = await turn3.dispatch("poke_back", {"user": 200000001})
    ck("第二次被拒", "已经戳过" in out3, out3)
    ck("只戳了一次", len([c for c in fake.calls if c[0] == "send_poke"]) == 1,
       str([c[0] for c in fake.calls]))

    print()
    print("== 被戳次数会累加（第一次可无视，反复戳要给一句）==")
    from core.pipeline import GateConfig, Pipeline
    p_gate = Pipeline(GateConfig.from_dict({"default_preset": "loose"}))
    import time as _t
    t0 = _t.time()
    hints = []
    for i in range(3):
        got.clear()
        await ch._handle_notice(Event(post_type="notice", self_id=3237702352, raw={
            "post_type": "notice", "notice_type": "notify", "sub_type": "poke",
            "group_id": 100000001, "user_id": 200000001, "target_id": 3237702352}))
        await asyncio.sleep(0.03)
        r = p_gate.run(got[0], t0 + i * 5)
        hints.append(" | ".join(r.hints))
    ck("第 2 次起提示已经戳了 N 次", any("已经戳了你 2 次" in h for h in hints), str(hints))
    ck("戳一戳不被合并", all("戳了" in h or "回不回随你" in h for h in hints), str(hints))

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
