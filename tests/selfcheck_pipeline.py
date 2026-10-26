#!/usr/bin/env python3
"""门控自测：三档位（strict/balanced/loose）+ 连续对话 + 功能开关 + 场面打分。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.channel import ChatRef, InboundMessage, Message, Sender  # noqa: E402
from core.pipeline import GateConfig, Pipeline  # noqa: E402
from core.policy import chat_key, resolve  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-44s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


def mk(text="今天天气不错。", *, gid=100000001, uid=111, at=False,
       private=False, room=False) -> InboundMessage:
    if room:
        chat = ChatRef.room(0, "breamer")
    elif private:
        chat = ChatRef.private(uid)
    else:
        chat = ChatRef.group(gid)
    return InboundMessage(chat=chat, sender=Sender(uid, "测试"),
                          message=Message.from_text(text), message_id="1",
                          addressed=at, addressed_by="at" if at else "")


def cfg(**kw) -> GateConfig:
    d = {"default_preset": "balanced", "dialogue_window_sec": 120,
         "credit_max": 3, "credit_refill_min": 15, "score_threshold": 2.0}
    d.update(kw)
    return GateConfig.from_dict(d)


print("== 白名单 ==")
p = Pipeline(cfg(allowed_groups=[100000001]))
ck("白名单外的群 → drop", p.run(mk(gid=999), time.time()).kind == "drop")
ck("白名单内的群 → 放行", p.run(mk(at=True), time.time()).kind != "drop")

print()
print("== strict：只认被叫到（豹群）==")
pol = resolve("balanced", {chat_key("group", 100000004): {"preset": "strict",
                                                          "memory": "off",
                                                          "history": False}},
              "group", 100000004)
ck("strict 档位", pol.mode == "strict", pol.mode)
ck("memory=off 不落盘", pol.store_memory is False)
ck("history 关掉", pol.history is False)
ck("strict 不追话", pol.continuation is False)
p = Pipeline(cfg(default_preset="strict"))
ck("strict 没被叫 → drop", p.run(mk("随便聊聊"), time.time()).kind == "drop")
ck("strict 被叫到 → reply", p.run(mk("在吗", at=True), time.time()).kind != "drop")

print()
print("== 连续对话 ==")
p = Pipeline(cfg())
t0 = time.time()
m1 = mk("在吗", at=True, uid=7)
r1 = p.run(m1, t0)
p.note_replied(m1, t0 + 1)
ck("被叫到会回", r1.kind != "drop", r1.reason)

r2 = p.run(mk("刚才说到哪了。", uid=7), t0 + 5)
ck("窗口内同一人 → 直接放行", r2.kind != "drop" and "对话" in r2.reason or r2.kind != "drop",
   r2.reason)
ck("理由里有『对话进行中』", "对话进行中" in (r2.decision.intent or r2.reason), r2.decision.intent)

r3 = p.run(mk("我是另一个人。", uid=8), t0 + 6)
ck("换人 → 回到打分（不必然回）", True, r3.reason)

r4 = p.run(mk("过了很久了。", uid=7), t0 + 400)
ck("窗口过期 → 回到打分", "对话进行中" not in r4.reason, r4.reason)

print()
print("== 发言额度（balanced）==")
p = Pipeline(cfg(credit_refill_min=600))     # 回充很慢，便于测"额度见底"
t = time.time()
# 她真的开口 4 次（额度 3 点，被叫到时照回，但额度会被扣光）
for i in range(4):
    m = mk("叫她。", at=True, uid=20 + i)
    p.run(m, t + i * 40)
    p.note_replied(m, t + i * 40 + 1)
st = p.state_for(mk().chat.ref)
ck("额度被扣光", st.credit < 1.0, "%.2f" % st.credit)
r = p.run(mk("群里另一个人说话。", uid=99), t + 300)
ck("额度见底 + 没被叫 → drop", r.kind == "drop" or "额度" in r.reason, r.reason)
m2 = mk("再叫她一次。", at=True, uid=99)
ck("被叫到时额度见底也回", p.run(m2, t + 320).kind != "drop")

print()
print("== loose：每条都问模型 ==")
p = Pipeline(cfg(default_preset="loose"))
kinds = [p.run(mk("闲聊一句。", uid=30 + i), time.time() + i * 10).kind for i in range(5)]
ck("loose 不因额度/打分被拦", all(k != "drop" for k in kinds), str(kinds))

print()
print("== 静默时段 / 成本上限 ==")
p = Pipeline(cfg(chats={chat_key("group", 100000001): {"quiet_hours": ["00:00-23:59"]}}))
ck("静默时段 → drop", p.run(mk("在吗", at=True), time.time()).kind == "drop")
p = Pipeline(cfg(max_decide_per_hour=1))
p.run(mk("一。", at=True), time.time())
r = p.run(mk("二。", at=True), time.time() + 1)
ck("decide 次数到顶 → drop", "decide" in r.reason, r.reason)

print()
print("== 种类兜底：全局 strict 不该让私聊/房间哑掉 ==")
from core.policy import resolve as _resolve  # noqa: E402

ck("私聊兜底 balanced", _resolve("strict", {}, "private", 1).mode == "balanced")
ck("房间兜底 loose", _resolve("strict", {}, "room", 0).mode == "loose")
ck("群保持 strict", _resolve("strict", {}, "group", 9).mode == "strict")
ck("显式覆盖优先", _resolve("strict", {chat_key("group", 9): {"preset": "loose"}},
                            "group", 9).mode == "loose")
p_priv = Pipeline(cfg(default_preset="strict"))
ck("strict 下私聊照样回", p_priv.run(mk("你好。", private=True), time.time()).kind != "drop")

print()
print("== 场面信号（避免冷场）==")
from core.signals import Scene, compute, score  # noqa: E402

sc = Scene()
now = time.time()
sc.note_incoming(now - 200, 5, "有人吗")
sig, hints = compute(sc, mk("有人吗"), ["大肥鱼"], dialogue_active=False,
                     budget_ratio=0.0, now=now)
ck("被晾 200 秒 → unanswered 信号", sig.get("unanswered") == 1.0, str(sig))
ck("分数被抬高", score(sig) > 0, "%.1f" % score(sig))
ck("提示里说了『没人接』", any("没人接" in h for h in hints), str(hints))
sig2, _ = compute(sc, mk("大肥鱼 在吗"), ["大肥鱼"], dialogue_active=False,
                  budget_ratio=0.0, now=now)
ck("提到名字 → name_nearby 信号", sig2.get("name_nearby") == 1.0, str(sig2))
ck("问句信号", compute(sc, mk("为什么啊？"), ["大肥鱼"], dialogue_active=False,
                       budget_ratio=0.0, now=now)[0].get("question") == 1.0)

print()
if FAIL:
    print("FAILED: %s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
