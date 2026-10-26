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


def mk(text: str, *, gid: int = 100000001, uid: int = 111, at: bool = False,
       owner: bool = False, private: bool = False) -> InboundMessage:
    chat = ChatRef.private(uid) if private else ChatRef.group(gid)
    return InboundMessage(chat=chat, sender=Sender(uid, "测试", is_owner=owner),
                          message=Message.from_text(text), message_id="1", addressed=at)


CFG = GateConfig.from_dict({
    "force_mode": "off",
    "allowed_groups": [100000001],
    "at_only_groups": [100000001],
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
p_sp = Pipeline(GateConfig.from_dict({"force_mode": "off", "reply_probability": 1.0,
                                      "speak_groups": [100000001]}))
ck("speak_groups 内的群：没 @ 也能接", p_sp.run(mk("今天天气不错。"), time.time()).kind == "reply")
p_sp0 = Pipeline(GateConfig.from_dict({"force_mode": "off", "reply_probability": 0.0,
                                       "speak_groups": [100000001]}))
ck("speak_groups 内但概率=0 → 不接", p_sp0.run(mk("今天天气不错。"), time.time()).kind == "silence")
p_sp_other = Pipeline(GateConfig.from_dict({"force_mode": "off", "reply_probability": 1.0,
                                            "speak_groups": [100000001]}))
ck("speak_groups 外的群：没 @ 仍然不接", p_sp_other.run(mk("今天天气不错。", gid=123456), time.time()).kind in ("drop", "silence"))
p_sp_at = Pipeline(GateConfig.from_dict({"force_mode": "off", "reply_probability": 1.0,
                                         "speak_groups": [100000001], "at_only_groups": [100000001]}))
ck("at_only 优先于 speak_groups", p_sp_at.run(mk("今天天气不错。"), time.time()).kind == "silence")
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
    "force_mode": "off", "allowed_groups": [100000001],
    "at_only_groups": [100000001], "private_owner_only": True,
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
    "force_mode": "off", "allowed_groups": [100000001],
    "at_only_groups": [100000001], "min_interval_sec": 0,
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
print("== 连续对话 ==")
CFG_D = dict(force_mode="off", allowed_groups=[100000001], reply_probability=0.0,
             min_interval_sec=8, burst_suppress_sec=30, max_auto_per_hour=99,
             dialogue_window_sec=90, dialogue_min_interval_sec=3)
t0 = time.time()
pd = Pipeline(GateConfig.from_dict(CFG_D))
base = mk("你好呀。")
ck("（起点）没 @ 且概率 0 → 不接", pd.run(base, t0).kind == "silence")
pd.note_replied(base, t0)                      # 模拟她刚刚开过口
o = pd.run(mk("你叫什么。"), t0 + 10)
ck("窗口内同一人 → 不抽样直接接", o.kind == "reply" and o.decision.intent == "对话进行中",
   "%s / %s" % (o.reason, o.decision.intent))
o = pd.run(mk("你叫什么。", uid=222), t0 + 12)
ck("窗口内换个人 → 仍按抽样（p=0 不接）", o.kind == "silence")
o = pd.run(mk("你叫什么。"), t0 + 200)
ck("窗口过期 → 回到抽样", o.kind == "silence")
p2 = Pipeline(GateConfig.from_dict(CFG_D))
m2 = mk("在的。")
p2.note_replied(m2, t0)
o = p2.run(mk("嗯。"), t0 + 12)                # 12s < burst_suppress 30s
ck("窗口内同一人连发不被连发抑制拦", o.kind == "reply", o.reason)
p3 = Pipeline(GateConfig.from_dict({**CFG_D, "dialogue_window_sec": 0}))
m3 = mk("在的。")
p3.note_replied(m3, t0)
o = p3.run(mk("你叫什么。", uid=333), t0 + 10)
ck("窗口=0 时行为不变（仍抽样）", o.kind == "silence", o.reason)


print()
if FAIL:
    print("FAILED: %s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
