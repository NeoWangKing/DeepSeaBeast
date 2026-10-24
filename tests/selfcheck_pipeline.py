#!/usr/bin/env python3
"""管线单测：不联网、不起服务，直接喂消息看"放行/吞掉/延后"。

用法：.venv/bin/python tests/selfcheck_pipeline.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.channel import ChatRef, InboundMessage, Message, Sender  # noqa: E402
from core.pipeline import GateConfig, Pipeline  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


def mk(text: str, *, gid: int = 1109525246, uid: int = 111, at: bool = False,
       owner: bool = False, private: bool = False) -> InboundMessage:
    chat = ChatRef.private(uid) if private else ChatRef.group(gid)
    return InboundMessage(chat=chat, sender=Sender(uid, "测试", is_owner=owner),
                          message=Message.from_text(text), message_id="1", addressed=at)


CFG = GateConfig.from_dict({
    "force_mode": "off",
    "allowed_groups": [1109525246],
    "at_only_groups": [1109525246],
    "merge_window_sec": 10,
    "burst_suppress_sec": 30,
    "min_interval_sec": 8,
    "max_auto_per_hour": 2,
})

print("== 白名单 ==")
p = Pipeline(CFG)
ck("白名单外的群 → drop", p.run(mk("在吗", gid=999), time.time()).kind == "drop")
ck("白名单内的群 + @ → reply", p.run(mk("在吗", at=True), time.time()).kind == "reply")

print()
print("== 只认 @ / 概率 ==")
p = Pipeline(CFG)
ck("群里没 @ → silence", p.run(mk("今天天气不错。"), time.time()).kind == "silence")
p2 = Pipeline(GateConfig.from_dict({"force_mode": "peak", "reply_probability": 1.0}))
ck("概率=1 时非 @ 也回", p2.run(mk("今天天气不错。"), time.time()).kind == "reply")
p3 = Pipeline(GateConfig.from_dict({"force_mode": "peak", "reply_probability": 0.0}))
ck("概率=0 时不回", p3.run(mk("今天天气不错。"), time.time()).kind == "silence")

print()
print("== 连发合并 ==")
p = Pipeline(CFG)
t0 = time.time()
r1 = p.run(mk("我跟你", at=True), t0)
ck("没说完先等着", r1.kind == "defer", r1.reason)
r2 = p.run(mk("说个事啊", at=True), t0 + 1)
ck("补完整后立刻回", r2.kind == "reply", r2.kind)
ck("回的是合并后的整句", r2.text == "我跟你 说个事啊", r2.text)
r3 = p.run(mk("算了不说了。", at=True), t0 + 15)
ck("隔久了重新开始", r3.kind == "reply")

print()
print("== 频率 ==")
_cfg_loose = GateConfig.from_dict({
    "force_mode": "off", "allowed_groups": [1109525246],
    "at_only_groups": [1109525246], "private_owner_only": True,
    "merge_window_sec": 10, "burst_suppress_sec": 30,
    "min_interval_sec": 8, "max_auto_per_hour": 10,
})
p = Pipeline(_cfg_loose)
now = time.time()
p.run(mk("在吗", at=True), now)
p.note_replied(mk("在吗", at=True), now)
ck("刚说完，别人说话被间隔挡住", p.run(mk("喂，在吗", uid=222), now + 1).kind == "drop")
ck("@她 不受间隔限制", p.run(mk("喂，在吗", uid=222, at=True), now + 1).kind == "reply")
p.note_replied(mk("喂，在吗", uid=222, at=True), now + 1)

# 额度：单独用一个 max_auto_per_hour=2 的配置测
p_cap = Pipeline(GateConfig.from_dict({
    "force_mode": "off", "allowed_groups": [1109525246],
    "at_only_groups": [1109525246], "min_interval_sec": 0,
    "burst_suppress_sec": 0, "max_auto_per_hour": 2,
}))
t2 = time.time()
for _ in range(2):
    m = mk("在吗", uid=777, at=True)
    p_cap.run(m, t2)
    p_cap.note_replied(m, t2)
r = p_cap.run(mk("第三次，在吗", uid=777, at=True), t2 + 1)
ck("每小时额度用完 → drop", r.kind == "drop" and "额度" in r.reason, r.reason)

print()
print("== 房间：被叫到一定回，没被叫到只偶尔接 ==")
from core.channel import ChatRef as _CR

def mk_room(text, addressed=False):
    return InboundMessage(chat=_CR.room(0, "breamer"), sender=Sender(9, "user9"),
                          message=Message.from_text(text), message_id="1",
                          addressed=addressed, addressed_by="name" if addressed else "")

cfg_room = GateConfig.from_dict({"force_mode": "off", "max_auto_per_hour": 999,
                                 "merge_window_sec": 0})
r_called = Pipeline(cfg_room).run(mk_room("大肥鱼 在吗", addressed=True), time.time())
ck("房间里叫她名字一定回", r_called.kind == "reply", r_called.reason)

# 抽样：每条都用一个全新 Pipeline（避免连发合并/额度干扰），统计比例
replies = 0
total = 60
for i in range(total):
    pr = Pipeline(cfg_room)
    if pr.run(mk_room("你们俩先聊。"), time.time() + i).kind == "reply":
        replies += 1
ck("没叫她就不是每条都回", replies < total, "60 条里回了 %d 条" % replies)
ck("但确实会偶尔接（抽样 ~25%%）", 0 < replies < total, "60 条里回了 %d 条" % replies)

print()
print("== 私聊 ==")
p = Pipeline(CFG)
ck("私聊陌生人默认也回", p.run(mk("你好", uid=444, private=True), time.time()).kind == "reply")
ck("私聊主人也回", p.run(mk("你好", uid=444, owner=True, private=True), time.time()).kind == "reply")
p_strict = Pipeline(GateConfig.from_dict({"force_mode": "off", "private_owner_only": True}))
ck("显式开启 owner_only 时才拦陌生人",
   p_strict.run(mk("你好", uid=444, private=True), time.time()).kind == "drop")

print()
if FAIL:
    print("FAILED: %s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
