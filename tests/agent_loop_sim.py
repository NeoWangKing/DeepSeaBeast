"""离线自测：agent/loop.py 的强制工具轮（注入假模型，不联网）。

用法：python3 tests/agent_loop_sim.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from agent import loop as agent_loop      # noqa: E402
from agent import tools as agent_tools    # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    print("  %-42s %s%s" % (name, "OK" if cond else "FAIL", (" " + str(extra)) if extra else ""))
    if not cond:
        FAIL.append(name)


def mk_tools():
    sent = []
    cb = {"send_text": lambda t, reply_to_id="", at_user_id="": sent.append(t),
          "send_sticker": lambda sid, reply_to_id="": (sent.append("STK:" + sid), True)[1],
          "recent_lines": lambda n: ["A：在吗"],
          "active_members": lambda: ["A"],
          "memory_lookup": lambda q: "印象",
          "list_stickers": lambda q: ["- s1 ｜ 猫"],
          "collect_sticker": lambda mid, note: "收好了"}
    return agent_tools.Tools("g1", cb, {}), sent


def schema_all():
    return agent_loop.spec_to_openai(agent_tools.spec_list({}, send_tools=True))


print("== schema ==")
sch = schema_all()
names = [x["function"]["name"] for x in sch]
check("工具都在", "send_message" in names and "finish" in names, names)
sm = [x for x in sch if x["function"]["name"] == "send_message"][0]
check("send_message 带 required+text",
      sm["function"]["parameters"]["required"] == ["text"]
      and "text" in sm["function"]["parameters"]["properties"])

print("== 循环：模型被强制调 send_message ==")
t, sent = mk_tools()
calls = []


def fake_chat(msgs, schema, n=1):
    calls.append(list(msgs))
    if len(calls) == 1:
        return {"content": "", "finish_reason": "tool_calls", "usage": {"total_tokens": 100},
                "tool_calls": [{"id": "c1", "name": "send_message",
                                "arguments": {"text": "在的 ||| 干嘛"}}]}
    return {"content": "", "finish_reason": "tool_calls", "usage": {"total_tokens": 50},
            "tool_calls": [{"id": "c2", "name": "finish", "arguments": {"reason": "说完了"}}]}


r = agent_loop.run(t, [{"role": "user", "content": "在吗"}], sch, chat_fn=fake_chat)
check("一轮就说了话", r["spoke"] and sent == ["在的", "干嘛"], "%s %s" % (r, sent))
check("说了话就收工（不再白跑一轮）", len(calls) == 1, len(calls))
check("token 用量累计", r["usage"].get("total_tokens") == 100, r["usage"])

print("== 循环：模型选择潜水 ==")
t2, sent2 = mk_tools()


def fake_finish(msgs, schema):
    return {"content": "", "finish_reason": "tool_calls", "usage": {},
            "tool_calls": [{"id": "c1", "name": "finish", "arguments": {"reason": "不想说"}}]}


r2 = agent_loop.run(t2, [{"role": "user", "content": "随便说点"}], sch, chat_fn=fake_finish)
check("finish → 没发东西", (not r2["spoke"]) and r2["finished"] and sent2 == [])
check("只跑一轮", r2["rounds"] == 1)

print("== 循环：空参数不再致命 ==")
t3, sent3 = mk_tools()


def fake_empty(msgs, schema):
    if len(msgs) < 3:
        return {"content": "", "finish_reason": "tool_calls", "usage": {},
                "tool_calls": [{"id": "c1", "name": "send_message", "arguments": {}}]}
    return {"content": "", "finish_reason": "tool_calls", "usage": {},
            "tool_calls": [{"id": "c2", "name": "send_message", "arguments": {"text": "补上了"}}]}


r3 = agent_loop.run(t3, [{"role": "user", "content": "hi"}], sch, chat_fn=fake_empty)
check("空参数被提示后重新调用成功", sent3 == ["补上了"], sent3)

print("== 循环：轮数上限 ==")
t4, sent4 = mk_tools()


def fake_loop(msgs, schema):
    return {"content": "", "finish_reason": "tool_calls", "usage": {},
            "tool_calls": [{"id": "c", "name": "get_recent_messages", "arguments": {"limit": "5"}}]}


r4 = agent_loop.run(t4, [{"role": "user", "content": "hi"}], sch, chat_fn=fake_loop, max_rounds=2)
check("最多 2 轮就收敛", r4["rounds"] == 2 and not r4["spoke"], r4["rounds"])

print()
if FAIL:
    print("失败 %d 项：%s" % (len(FAIL), "、".join(FAIL)))
    sys.exit(1)
print("全部通过")
