"""离线验证：未完成事项（"我再看看"要记下来、下次注入、过会儿自己回来补）。

跑法：python3 tests/pending_sim.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from agent import pending as P      # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-50s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


print("== 新鲜度 ==")
ck("刚记的算数", P.fresh(int(time.time())))
ck("40 分钟前的过期", not P.fresh(int(time.time()) - 2400))
ck("没有时间戳 → 不算数", not P.fresh(None))

print("== 提示词片段 ==")
_h = P.hint("@你 查一下绿龙下一场比赛是什么时候", "HLTV 上写的，具体几点没标，我再看看是哪个赛事")
ck("带上了原问题和她的承诺", "绿龙" in _h and "我再看看" in _h)
ck("说明被催就是这件事", "你怎么不继续说了" in _h)
ck("要求查不到就说没查到", "没查到" in _h)
ck("空承诺 → 空串", P.hint("问", "") == "")

print("== 落盘结构（纯函数） ==")
_d = P.merge({}, "869622030", "问题", "承诺")
ck("写进一个 key", "869622030" in _d and _d["869622030"]["q"] == "问题")
_d2 = P.merge(_d, "p:1", "问题2", "承诺2")
ck("多条并存", len(_d2) == 2)
_d3 = P.merge({}, "k", "q", "p", limit=1)
ck("超上限只留最新", len(_d3) == 1)

print("== 接线在位 ==")
_src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("有存取与注入", all(k in _src for k in ("_pending_text", "_pending_set", "_pending_clear", "_pending_wake")))
ck("普通轮注入了未完成事项", "未完成事项：明明白白摆出来" in _src)
ck("主动轮也注入", "_pt2 = self._pending_text(gid)" in _src)
ck("留尾巴会记下并定唤醒", "（并定 90 秒后自己回来补）" in _src)
ck("给结论就清掉", "未完成事项：这轮给了结论 → 清掉" in _src)
_sec = open(os.path.join(ROOT, "promptlib", "sections.py"), encoding="utf-8").read()
ck("提示词教她被催时接着做完", "那是在催你之前答应过的事" in _sec)
ck("明确禁止糊过去", "别答「说什么」" in _sec or "说什么" in _sec)

print()
if FAIL:
    print("有问题：%s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
