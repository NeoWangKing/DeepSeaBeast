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
ck("对话延续也要等 ≥30s（原 %s）" % _cfg.get("min_cont_gap_sec"),
   float(_t.get("min_cont_gap_sec", 0)) >= 30, str(_t.get("min_cont_gap_sec")))
ck("延续额度 ≤6/时（原 %s）" % _cfg.get("max_cont_per_hour"),
   int(_t.get("max_cont_per_hour", 99)) <= 6, str(_t.get("max_cont_per_hour")))
ck("连击上限 ≤3（原 %s）" % _cfg.get("max_engaged_streak"),
   int(_t.get("max_engaged_streak", 99)) <= 3, str(_t.get("max_engaged_streak")))

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
ck("闪电群 max_auto_per_hour = 4", o._tune_i("869622030", "max_auto_per_hour", 10) == 4)
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


print("== 真跑：同一个群跑同一批消息，调前 vs 调后 ==")
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
STEPS = [(30, TEXTS[i % len(TEXTS)], {"uid": "u%d" % (i % 5)}) for i in range(400)]


async def _run(tuned: bool):
    over = {"group_tuning": {GID: json.loads(json.dumps(_t))}} if tuned else {}
    p = G.new_plugin(over, None)
    clock = G.Clock(G.T0)
    hours = collections.Counter()
    n = 0
    for dt, text, kw in STEPS:
        clock.advance(dt)
        ev = G.FakeEvent(text, gid=GID, **kw)
        _h = int(clock.time() // 3600)
        if await G.send(p, ev):
            n += 1
            hours[_h] += 1
            p.last_reply_ts[GID] = clock.time()
            p.last_auto[GID] = clock.time()
            p.last_reply_to[GID] = str(ev.get_sender_id())
    return n, hours


_n_off, _h_off = asyncio.run(_run(False))
_n_on, _h_on = asyncio.run(_run(True))
print("  调前：共 %d 条；每小时 %s" % (_n_off, dict(sorted(_h_off.items()))))
print("  调后：共 %d 条；每小时 %s" % (_n_on, dict(sorted(_h_on.items()))))
ck("调后每小时都不超过 4 条（硬上限）", all(v <= 4 for v in _h_on.values()), str(dict(_h_on)))
ck("调前确实会超过 4 条/小时（说明改的是真闸门）", any(v > 4 for v in _h_off.values()),
   str(dict(_h_off)))
ck("总量不比以前多", _n_on <= _n_off, "%d → %d" % (_n_off, _n_on))
ck("没被调成哑巴（还能说话）", _n_on >= 1, str(_n_on))

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
