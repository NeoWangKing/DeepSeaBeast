"""离线验证：发送口"像工具调用/协议描述"的拦截 + 连发合并不再吞 @。

跑法：python3 tests/meta_guard_sim.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import replyproto as R      # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


print("== 该拦的（工具调用/协议描述，不许发出去） ==")
BADS = [
    "send_message 的调用参数里要发的话是「这表情怎么有点像我」。",   # 真实事故原句
    "调用 send_message，参数 text=「在的」",
    "我要调 send_message 说这句话",
    "tool_calls: [{\"name\": \"send_message\"}]",
    "{'name': 'send_message', 'arguments': {}}",
    "发消息工具的参数里要发的话是「哈哈」",
    "图：一只黑猫，拇指向上",
    "收：是",
    "回：在的",
    "服务器繁忙，请稍后再试",          # 真实事故：她把系统报错当话说
    "请稍后再试",
    "请求失败",
    "网络异常，请稍后重试",
]
for t in BADS:
    ck("拦 %s" % t[:30], R.looks_like_meta(t))

print("== 不该拦的（正常聊天） ==")
GOODS = [
    "这表情怎么有点像我",
    "哈哈哈哈笑死",
    "我觉得这个参数得再调调",
    "图个乐而已",
    "今晚八点打本，来不来",
    "回你一句：确实",
    "我在摸鱼，干嘛",
    "凯尔希没了永生这事我一直没缓过来",
    "",
    "arguments 这个词我不会拼",
    "我觉得服务器繁忙是官方的锅",       # 正常讨论，别误伤
    "它老说请稍后再试，挺烦的",
]
for t in GOODS:
    ck("放 %s" % (t[:30] or "（空串）"), not R.looks_like_meta(t))

print("== 长文本不误拦（人真会发长句） ==")
long_ok = "今天群里聊到凯尔希的永生问题，我觉得这事得看剧情怎么写，" * 6
ck("超长正常文本不拦", not R.looks_like_meta(long_ok), "%d 字" % len(long_ok))

print("== main.py 的两处修复在位 ==")
src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("发送口调用拦截", "replyproto.looks_like_meta(text)" in src)
ck("看图轮兜底也过滤", "看图轮：剩下的是工具/协议描述" in src)
ck("看图轮去掉工具说明", "看图轮不用工具把\"工具清单" in src or "_vsys2" in src)
ck("合并前判断必回", "_must_reply_now = bool(self._at_me(event) or self._quotes_me(event))" in src)
ck("必回不被合并吞", "但这条 @了她/引用了她 → 不合并" in src)

print()
if FAIL:
    print("有问题：%s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
