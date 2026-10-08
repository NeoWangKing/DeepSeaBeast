"""离线验证：对话延续放行（同一个人接着说不用再 @）。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from scoring import cont_pass      # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


NOW = time.time()
LAST = NOW - 20            # 她 20 秒前回过话
ck("同一个人接着说 → 放行", cont_pass(NOW, LAST, "u1", "u1", False, False))
ck("引用她 → 放行", cont_pass(NOW, LAST, "u2", "", True, False))
ck("内容在接她的话（overlap 命中）→ 放行", cont_pass(NOW, LAST, "u2", "", False, True))
ck("陌生人说无关的话 → 不回", not cont_pass(NOW, LAST, "u2", "", False, False))
ck("刚过 2 秒（太急，防连刷）→ 不回", not cont_pass(NOW, NOW - 2, "u1", "u1", False, False))
ck("超过 180 秒（不算同一段对话）→ 不回",
   not cont_pass(NOW, NOW - 200, "u1", "u1", False, False))
ck("她还没说过话 → 不回", not cont_pass(NOW, 0, "u1", "u1", False, False))
ck("没有发送者 → 不回", not cont_pass(NOW, LAST, "", "u1", False, False))
ck("参数缺失也不炸", cont_pass(None, None, None, None, None, None) is False)
ck("gap 可调：间隔 3 秒 + gap=2 → 放行", cont_pass(NOW, NOW - 3, "u1", "u1", False, False, gap=2))

_src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("gate 里有对话延续分支", "对话延续：他接着说" in _src)
ck("用的是 cont_pass", "scoring.cont_pass(" in _src)
ck("有独立每小时上限", "max_cont_per_hour" in _src)
import json  # noqa: E402
_cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
ck("配置里有 min_cont_gap_sec / max_cont_per_hour",
   "min_cont_gap_sec" in _cfg and "max_cont_per_hour" in _cfg,
   "%s / %s" % (_cfg.get("min_cont_gap_sec"), _cfg.get("max_cont_per_hour")))

print()
if FAIL:
    print("有问题：%s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
