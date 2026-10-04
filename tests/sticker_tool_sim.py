"""离线验证：发表情这条链（工具参数 / 空参数兜底 / schema required）。

跑法：python3 tests/sticker_tool_sim.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from agent import tools as T          # noqa: E402
from agent import loop as L           # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


print("== 发表情工具 ==")
got = []
cb = {"send_sticker": lambda sid, mood="", reply_to_id="": (got.append((sid, mood, reply_to_id)) or True)}
t = T.Tools("869622030", cb, {"max_calls": 5})

ck("指名 id 发", t.send_sticker("s1", reply_to_id="9") == "已发出表情 s1")
ck("按情绪挑（id 留空）", t.send_sticker(mood="想笑") == "已发出表情（按「想笑」自己挑的一张）")
ck("两个都没给也能发", t.send_sticker() == "已发出表情（按「随手」自己挑的一张）")
ck("指名那次的参数", got[0] == ("s1", "", "9"), str(got[0]))
ck("情绪透传到回调", got[1] == ("", "想笑", ""), str(got[1]))
ck("两个都没给时不报错", got[2] == ("", "", ""), str(got[2]))
ck("记进 sent（便于判 spoke）", len([x for x in t.sent if x[0] == "sticker"]) == 3)

print("== schema：sticker_id 不再必填（可以改用 mood） ==")
specs = [("send_sticker", [{"type": "string", "name": "sticker_id"},
                           {"type": "string", "name": "mood"}], "发一张表情", "send_sticker")]
sc = L.spec_to_openai(specs)[0]
ck("send_sticker.required 为空", sc["function"]["parameters"]["required"] == [])
ck("mood 进了 properties", "mood" in sc["function"]["parameters"]["properties"])
ck("send_message.text 仍必填",
   L.spec_to_openai([("send_message", [{"type": "string", "name": "text"}], "x", "send_message")])[0]
   ["function"]["parameters"]["required"] == ["text"])

print("== 挑图：情绪词能对上标签 ==")
try:
    import stickers as S
    n = len(S.load())
    if n:
        ck("pick(无语) 有结果", bool(S.pick("无语", limit=3)), "库内 %d 张" % n)
        ck("pick(好耶) 走同义", bool(S.pick("好耶", limit=3)))
    else:
        print("  （本地库为空，跳过 pick 检查）")
except Exception as e:
    print("  pick 检查跳过：%r" % (e,))

print()
if FAIL:
    print("有问题：%s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
