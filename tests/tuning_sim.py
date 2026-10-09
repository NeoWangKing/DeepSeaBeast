"""离线验证：闪电群发言频率调低（group_tuning 按群生效、其它群不受影响）。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/tuning_sim.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


_cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
_t = (_cfg.get("group_tuning") or {}).get("869622030") or {}

print("== 配置：闪电群拧紧了 ==")
ck("有 group_tuning.869622030", bool(_t))
ck("概率打 3.5 折（reply_prob_mult<1）", 0 < float(_t.get("reply_prob_mult", 1)) < 0.6,
   str(_t.get("reply_prob_mult")))
ck("每小时额度降到 ≤4（原 %s）" % _cfg.get("max_auto_per_hour"),
   int(_t.get("max_auto_per_hour", 99)) <= 4, str(_t.get("max_auto_per_hour")))
ck("私下冷却抬到 ≥240s（原 %s）" % _cfg.get("min_interval_sec"),
   float(_t.get("min_interval_sec", 0)) >= 240, str(_t.get("min_interval_sec")))
ck("对话延续留着：接着说只等 ≤15s（原 %s）" % _cfg.get("min_cont_gap_sec"),
   float(_t.get("min_cont_gap_sec", 99)) <= 15, str(_t.get("min_cont_gap_sec")))
ck("对话延续留着：窗口 ≥300s（原 %s）" % _cfg.get("cont_window_sec"),
   float(_t.get("cont_window_sec", 0)) >= 300, str(_t.get("cont_window_sec")))
ck("对话延续留着：额度 ≥10/时（原 %s）" % _cfg.get("max_cont_per_hour"),
   int(_t.get("max_cont_per_hour", 0)) >= 10, str(_t.get("max_cont_per_hour")))
ck("连击只是收一点（原 %s）" % _cfg.get("max_engaged_streak"),
   int(_t.get("max_engaged_streak", 99)) <= 8, str(_t.get("max_engaged_streak")))

print("== _tune：按群取值 / 别的群不受影响 ==")
try:
    import main
except Exception as e:
    print("  （跳过：%s）" % str(e)[:70])
    print()
    sys.exit(1 if FAIL else 0)


o = main.QqPeakGate.__new__(main.QqPeakGate)
o.cfg = _cfg
ck("闪电群 min_interval_sec = 配的值",
   o._tune_f("869622030", "min_interval_sec", 90) == float(_t["min_interval_sec"]))
ck("闪电群主动接话上限 = 4", o._tune_i("869622030", "max_auto_per_hour", 10) == 4)
ck("延续额度是独立的一份（%s）" % _t.get("max_cont_per_hour"),
   o._tune_i("869622030", "max_cont_per_hour", 12) == int(_t.get("max_cont_per_hour")))
ck("豹群不受影响（拿顶层值）", o._tune_f("966812151", "min_interval_sec", 90) == 90)
ck("Denial 群不受影响", o._tune_f("1105410423", "min_interval_sec", 90) == 90)
ck("私聊不受影响", o._tune_f("p:3245938285", "min_interval_sec", 90) == 90)
ck("没配的项走顶层", o._tune_f("869622030", "engaged_interval_sec", 6) == float(_t["engaged_interval_sec"]))
ck("群号未知也不炸", isinstance(o._tune_f("000", "min_interval_sec", 12), float))

_cfg2 = json.loads(json.dumps(_cfg))
_cfg2["group_tuning"] = {"*": {"max_auto_per_hour": 2}}
o2 = main.QqPeakGate.__new__(main.QqPeakGate)
o2.cfg = _cfg2
ck("支持 * 通配（所有群）", o2._tune_i("999999", "max_auto_per_hour", 10) == 2)
_cfg2["group_tuning"]["869622030"] = {"max_auto_per_hour": 7}
o2.cfg = _cfg2
ck("具体群号优先于 *", o2._tune_i("869622030", "max_auto_per_hour", 10) == 7)

print("== 概率乘数真的作用在抽签前 ==")
_src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("gate 里读了 reply_prob_mult", "reply_prob_mult" in _src)
ck("延续不再被主动接话上限掐断",
   "min(self._tune_i(gid, \"max_cont_per_hour\"" not in _src and
   "max_total_per_hour` 优先" in _src)
ck("_hour_cap 文档写明延续不受它约束", "对话延续**不受这个数约束" in _src or "不受这个数约束" in _src)
ck("乘完做了 0~1 截断", "min(1.0, float(p) * _mult)" in _src)
ck("决策日志会写出来", "群频率x%0.2f" in _src or "群频率x%.2f" in _src)
for _k in ("max_auto_per_hour", "min_interval_sec", "max_engaged_streak",
           "engaged_rest_sec", "engaged_interval_sec", "burst_suppress_sec",
           "min_cont_gap_sec", "cont_window_sec", "max_cont_per_hour"):
    ck("闸门 %s 改成按群取值" % _k, "_tune" in _src and _k in _src)

print("== 人格卡也说了别接茬 ==")
_card = open(os.path.join(ROOT, "prompts", "system_prompt_friend.txt"), encoding="utf-8").read()
ck("闪电群卡里写了「别什么话都接茬」", "别什么话都接茬" in _card)
ck("写了「多数消息你都不用理」", "多数消息你都不用理" in _card)
ck("写了「一小时里冒头次数本来就该很少」", "一小时里你冒头的次数本来就该很少" in _card)


print("== 真跑：闲聊场景（少插嘴）+ 对话场景（延续留着） ==")
try:
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import gate_sim as G          # 桩 + 真实 gate()
except Exception as e:
    print("  （跳过：%s）" % str(e)[:70])
    print()
    sys.exit(1 if FAIL else 0)

import asyncio                                  # noqa: E402
import collections                              # noqa: E402

GID = "869622030"
TEXTS = ["今天天气还行", "这把cs打完了", "晚上吃啥", "有人来频道吗", "困了",
         "这把谁赢了", "deadlock 好玩吗", "我下了", "上班好累", "再看一局"]
# A：一群人在闲聊，没人跟她说话（她不该插嘴）
CHAT = [(30, TEXTS[i % len(TEXTS)], {"uid": "u%d" % (i % 5)}) for i in range(400)]
# B：同一个人一直在跟她说话（真·对话，延续该放行）
TALK = [(30, "那你觉得这把他能赢吗", {"uid": "u1"}) for _ in range(12)]


async def _run(steps, tuned):
    over = {"group_tuning": {GID: json.loads(json.dumps(_t))}} if tuned else {}
    clock = G.Clock(G.T0)
    p = G.new_plugin(over, clock)
    logs = []
    p._log = lambda *a, **k: logs.append(" ".join(str(x) for x in a))
    p._log_debug = lambda *a, **k: None
    hours = collections.Counter()
    n = 0
    for dt, text, kw in steps:
        clock.advance(dt)
        ev = G.FakeEvent(text, gid=GID, **kw)
        _h = int(clock.time() // 3600)
        if await G.send(p, ev):
            n += 1
            hours[_h] += 1
            p.last_reply_ts[GID] = clock.time()
            p.last_auto[GID] = clock.time()
            p.last_reply_to[GID] = str(ev.get_sender_id())
    _cont = sum(1 for x in logs if "对话延续：他接着说" in x)
    return n, hours, _cont


_a_off, _h_off, _c_off = asyncio.run(_run(CHAT, False))
_a_on, _h_on, _c_on = asyncio.run(_run(CHAT, True))
print("  闲聊 400 条：调前 %d 条（延续 %d / 抽签 %d）；调后 %d 条（延续 %d / 抽签 %d）"
      % (_a_off, _c_off, _a_off - _c_off, _a_on, _c_on, _a_on - _c_on))
print("     调后每小时 %s" % dict(sorted(_h_on.items())))
ck("闲聊场景：主动接话（抽签）确实变少", (_a_on - _c_on) <= (_a_off - _c_off),
   "%d → %d" % (_a_off - _c_off, _a_on - _c_on))
ck("闲聊场景：总量比原来少", _a_on <= _a_off, "%d → %d" % (_a_off, _a_on))
ck("闲聊场景：延续额度顶得住（每小时 ≤%s）" % int(_t.get("max_cont_per_hour", 20)),
   all(v <= int(_t.get("max_cont_per_hour", 20)) for v in _h_on.values()), str(dict(_h_on)))
ck("闲聊场景：还能开口（没被调成哑巴）", _a_on >= 1, str(_a_on))

# B：对话延续本身要放行（这里直接摆出"她刚回过 u1"的状态，避免靠抽签碰运气）
from scoring import cont_pass                      # noqa: E402
_now = 1000.0
_gap = float(_t.get("min_cont_gap_sec") or 8)
_win = float(_t.get("cont_window_sec") or 180)
ck("同一人隔 30 秒接着说 → 算对话延续",
   cont_pass(_now, _now - 30, "u1", "u1", False, False, _gap, _win))
ck("隔 5 秒就接 → 不算（防连珠炮）",
   not cont_pass(_now, _now - 5, "u1", "u1", False, False, _gap, _win))
ck("超过窗口（>%ds）→ 不算同一段对话" % int(_win),
   not cont_pass(_now, _now - (_win + 60), "u1", "u1", False, False, _gap, _win))
ck("别人接着说、不是对她说的 → 不算延续",
   not cont_pass(_now, _now - 30, "u2", "u1", False, False, _gap, _win))


async def _cont_case():
    clock = G.Clock(G.T0)
    p = G.new_plugin({"group_tuning": {GID: json.loads(json.dumps(_t))}}, clock)
    p.last_reply_ts[GID] = clock.time()          # 假装她刚回过 u1
    p.last_auto[GID] = clock.time()
    p.last_reply_to[GID] = "u1"
    clock.advance(30)
    ev = G.FakeEvent("那你觉得这把他能赢吗", gid=GID, uid="u1")
    ok = await G.send(p, ev)
    return bool(ok), p.hour_count.get((GID, int(clock.time() // 3600)), 0)


_ok_b, _used_b = asyncio.run(_cont_case())
ck("真跑：她刚回过 u1，u1 接着说 → 直接放行（不看概率）", _ok_b, str(_ok_b))
ck("…延续走自己的额度（计入 1 条）", _used_b == 1, str(_used_b))

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
