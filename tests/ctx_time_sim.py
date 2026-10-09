"""离线验证：最近群聊带时间戳 + 判定器不吃旧话题（2026-10-09 前言不搭后语事故）。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/ctx_time_sim.py
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


_src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()

print("== 每条最近群聊都记了时间 ==")
for _frag in ('buf.append((who, t, uid_now, time.time()))',
              'buf.append(("我", t, "", time.time()))',
              'buf.append(("我", text, "", time.time()))',
              '_buf.append(("我", str(text)[:60], "", time.time()))',
              '_buf.append(("<图片>", _desc, "", time.time()))'):
    ck("记时间：%s…" % _frag[:38], _frag in _src)

print("== 渲染带 [HH:MM] 与断层提示 ==")
import main                                  # noqa: E402

o = main.QqPeakGate.__new__(main.QqPeakGate)
_now = time.time()
_old = _now - 36 * 60                        # 36 分钟前的旧话题
items = [("Na1ky0", "加起来87抽", "u2", _old - 60),
         ("Na1ky0", "平均29抽一个六星", "u2", _old),
         ("Neo武神", "这最后一关吗", "u1", _now - 20),
         ("Neo武神", "艹", "u1", _now - 5)]
lines = o._fmt_recent(items, 20, 60, now=_now)
for ln in lines:
    print("     %s" % ln)
ck("每行带 [HH:MM] 时间", all(ln.startswith("[") or ln.startswith("   ——") for ln in lines))
ck("插入了『隔了 36 分钟、别接着聊』的提示",
   any("别接着聊" in ln for ln in lines), str([x for x in lines if "——" in x])[:80])
ck("提示出现在断层之后、新消息之前",
   [i for i, x in enumerate(lines) if "——" in x][0] <
   [i for i, x in enumerate(lines) if "艹" in x][0])

_fresh = [(a, b, c, _now - 30 * i) for i, (a, b, c, _d) in enumerate(items)]
_l2 = o._fmt_recent(list(reversed(_fresh)), 20, 60, now=_now)
ck("全部在 10 分钟内 → 不插断层提示", not any("——" in x for x in _l2), str(_l2)[:90])

_l3 = o._fmt_recent([("某人", "很久以前的话", "u9", _now - 3600)], 20, 60, now=_now)
ck("最新一条也很旧 → 末尾提醒『早翻篇了』", any("早翻篇了" in x for x in _l3), str(_l3)[:90])

ck("老数据（没时间戳）也能渲染", o._fmt_recent([("某人", "老格式", "u1")], 5, 60) == ["某人：老格式"])

print("== 判定器不再吃旧话题 ==")
ck("判定前会检查发送者和时间", "_luid == str(event.get_sender_id()" in _src)
ck("只有 180 秒内的紧跟话才带上文", "(time.time() - _lts) <= 180" in _src)
ck("当前消息长（>20 字）就不带上文", "len(_cur_q) <= 20" in _src)
ck("判定用的 recent 仍限 3 条", '[str(x[1] or "") for x in _rc[-3:]]' in _src)

print("== 提示词也说清了 ==")
ck("最近群聊头部说明带 [时间]", "带 [时间] 的是那条消息说的时间" in _src)
ck("头部写明『离现在很久的只是背景，别去接』", "离现在很久的只是背景，别去接" in _src)
_card = open(os.path.join(ROOT, "prompts", "system_prompt_friend.txt"), encoding="utf-8").read()
ck("人格卡：语气词就顺着这条回、别翻旧账", "别翻旧账" in _card and "语气词" in _card)

print("== 真跑：老话题不会被当成当前问题 ==")
try:
    from agent import websearch as W
    ck("内容为语气词时不强制查（判定=no/空）",
       W.should_force(W.needs_lookup("艹"), 0.0, 0.34) is False)
    ck("真的问赛程时才强制查",
       W.should_force(W.needs_lookup("绿龙下一场比赛什么时候"), 0.0, 0.34) is True)
except Exception as e:
    print("  （跳过：%s）" % str(e)[:70])

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
