"""离线验证：可查的事实问题要先去查（判定函数 + 提示词 + 兜底在位）。

跑法：python3 tests/search_nudge_sim.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from agent import websearch as W      # noqa: E402
from promptlib import sections as SEC  # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-50s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


print("== 该判定成「可查的事实问题」 ==")
LOOKUPS = ["那终末地下一个版本是什么时候更新",     # 真实案例
           "明日方舟下个活动什么时候开",
           "新版本几号上线？",
           "IEM 科隆谁赢了",
           "下周赛程打谁",
           "这皮肤多少钱",
           "终末地什么时候公测呢",
           "最新公告出了吗",
           "复刻卡池是哪天"]
for t in LOOKUPS:
    ck("查 %s" % t[:24], W.is_lookup_question(t))

print("== 不该判定成事实问题（聊天/观点/玩笑） ==")
NOT_LOOKUPS = ["哈哈哈哈笑死", "我在摸鱼", "你说得对", "这图什么意思", "我觉得挺好玩的",
               "今天好累啊", "凯尔希没了永生这事我一直没缓过来", "", "晚点再聊"]
for t in NOT_LOOKUPS:
    ck("不查 %s" % (t[:24] or "（空串）"), not W.is_lookup_question(t))
ck("超长文本不误判", not W.is_lookup_question("什么时候更新" * 30))

print("== 「不知道」式回答识别 ==")
for t in ["不知道，我又不是鹰角内部人员", "不清楚", "没听说啊", "我哪知道", "等官方吧",
          "我搜了下没找到", "我搜了下没找到，这会儿网也断着", "搜不到", "没查到"]:
    ck("装傻 %s" % t[:20], W.is_dontknow(t))
for t in ["我觉得下个月吧", "查了，大概是 11 月，官方公告写的", "哈哈", ""]:
    ck("不算装傻 %s" % (t[:20] or "（空串）"), not W.is_dontknow(t))

print("== 提示词硬规则 ==")
txt = SEC._search_rules({"caps": {"search": True}})
ck("开了搜索才注入", txt.startswith("【没把握就查"))
ck("写明「不知道」不算回答", "不算回答" in txt and "我又不是内部人员" in txt)
ck("写明查不到就直说", "我搜了下没找到" in txt)
ck("关掉搜索就不注入", SEC._search_rules({"caps": {"search": False}}) == "")

print("== main.py 里的两层兜底在位 ==")
src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("当轮提醒可查问题", "【这是可查的事实问题】" in src)
ck("答不知道且没查 → 补一轮", "查证兜底：可查的问题没查就答" in src)
ck("补轮提示词", "先调 web_search 查一下，再补一句自然的回复" in src)
ck("故障计数 no_search_answer", '_fault("no_search_answer"' in src)
ck("工具调用打到 INFO（可审计）", "agent loop：本轮调用 → " in src)
ck("提示词禁止编造搜索过程", "没调 web_search 就不能说" in SEC._search_rules({"caps": {"search": True}}))

print()
if FAIL:
    print("有问题：%s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
