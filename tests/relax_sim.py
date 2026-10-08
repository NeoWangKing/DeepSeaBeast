"""离线验证：token 预算放宽了（这些数字是"真人感"相关，别再被悄悄收紧）。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FAIL = []


def ck(name, cond, extra=""):
    print("  %-50s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


c = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
ag = c.get("agent") or {}
kb = c.get("kb") or {}
st = c.get("search") or {}
cx = c.get("context") or {}

print("== 连续对话（放宽带） ==")
ck("同一段对话窗口 ≥ 600s", int(c.get("cont_window_sec", 0) or 0) >= 600, c.get("cont_window_sec"))
ck("最小间隔 ≤ 3s（能自然接话）", float(c.get("min_cont_gap_sec", 99) or 99) <= 3, c.get("min_cont_gap_sec"))
ck("对话延续每小时 ≥ 30 条", int(c.get("max_cont_per_hour", 0) or 0) >= 30, c.get("max_cont_per_hour"))
ck("主动接话每小时 ≥ 10 条", int(c.get("max_auto_per_hour", 0) or 0) >= 10, c.get("max_auto_per_hour"))
ck("普通冷却 ≤ 90s", int(c.get("min_interval_sec", 999) or 999) <= 90, c.get("min_interval_sec"))
ck("engaged 窗口 ≥ 180s", int(c.get("engaged_window_sec", 0) or 0) >= 180, c.get("engaged_window_sec"))
ck("engaged 冷却 ≤ 30s", int(c.get("engaged_rest_sec", 999) or 999) <= 30, c.get("engaged_rest_sec"))
ck("连击上限 ≥ 12", int(c.get("max_engaged_streak", 0) or 0) >= 12, c.get("max_engaged_streak"))

print("== 上下文 / 记忆 ==")
ck("最近群聊 ≥ 20 条", int(cx.get("recent_lines", 0) or 0) >= 20, cx.get("recent_lines"))
ck("每行 ≥ 100 字", int(cx.get("line_chars", 0) or 0) >= 100, cx.get("line_chars"))
ck("她说过的 ≥ 10 句", int(cx.get("mine_lines", 0) or 0) >= 10, cx.get("mine_lines"))
ck("每句 ≥ 60 字", int(cx.get("mine_chars", 0) or 0) >= 60, cx.get("mine_chars"))
ck("记忆注入 ≥ 20 条", int(cx.get("memory_notes", 0) or 0) >= 20, cx.get("memory_notes"))
ck("资料库 top_k ≥ 4", int(kb.get("top_k", 0) or 0) >= 4, kb.get("top_k"))
ck("资料库 max_chars ≥ 1500", int(kb.get("max_chars", 0) or 0) >= 1500, kb.get("max_chars"))

print("== 轮数 / 输出 / 搜索 ==")
ck("max_rounds ≥ 5", int(ag.get("max_rounds", 0) or 0) >= 5, ag.get("max_rounds"))
ck("max_sends_per_turn ≥ 8", int(ag.get("max_sends_per_turn", 0) or 0) >= 8, ag.get("max_sends_per_turn"))
ck("搜索一轮 ≥ 4 次", int(st.get("max_per_turn", 0) or 0) >= 4, st.get("max_per_turn"))
ck("读原文 ≥ 6000 字", int(st.get("read_max_chars", 0) or 0) >= 6000, st.get("read_max_chars"))
ck("表情清单 ≥ 20 张", int((c.get("stickers") or {}).get("prompt_max", 0) or 0) >= 20)

print("== main.py 真的读这些配置 ==")
_src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
for k in ("recent_lines", "line_chars", "mine_lines", "mine_chars", "memory_notes"):
    ck("代码读 config.context.%s" % k, ('"%s"' % k) in _src)

print("== 大致增量估算（字数） ==")
_add = (cx.get("recent_lines", 20) * cx.get("line_chars", 100)
        + cx.get("mine_lines", 10) * cx.get("mine_chars", 60)
        + cx.get("memory_notes", 20) * 30)
print("  上下文/记忆上限约 %d 字（实际按聊天量走，不会每轮都满）" % _add)

print()
if FAIL:
    print("有问题：%s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
