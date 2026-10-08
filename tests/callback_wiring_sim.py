"""离线验证：工具回调"接线"完整——tools.py 里 cb.get("X") 用到的每个键，
main.py 的 _agent_callbacks 必须真的提供。

为什么要有这个测试：2026-10-07 web_search/read_url/use_skill 三个工具定义了回调函数
却忘了登记进回调表，工具调用一律返回"不能联网搜索"，白折腾了小半天。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from agent import tools as T      # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


tools_src = open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read()
main_src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()

need = set(re.findall(r'self\.cb\.get\("([a-z_]+)"\)', tools_src))
# send_ok 是按会话动态给的开关，不算"回调"
OPTIONAL = {"send_ok"}

i = main_src.index("    def _agent_callbacks(self, target)")
j = main_src.index("\n    def ", i + 10)
body = main_src[i:j]
provided = set()
for _m in re.finditer(r"cbs\s*=\s*\{([^}]*)\}", body):
    provided |= set(re.findall(r'"([a-z_]+)"\s*:', _m.group(1)))
for _m in re.finditer(r"cbs\.update\(\{([^}]*)\}\)", body):
    provided |= set(re.findall(r'"([a-z_]+)"\s*:', _m.group(1)))

print("== 工具需要的回调 ==")
print("  tools.py 用到:", ", ".join(sorted(need)))
print("  main 提供  :", ", ".join(sorted(provided)))
missing = sorted(need - OPTIONAL - provided)
ck("没有漏接的回调", not missing, "缺失: %s" % (", ".join(missing) if missing else "无"))

extra = sorted(provided - need - OPTIONAL)
if extra:
    print("  （main 多给的，无害）:", ", ".join(extra))

print("== _agent_callbacks 里不许引用外函数的变量 ==")
_bad_names = [n for n in ("_akey",) if n in body]
ck("没有引用 _akey（曾导致每轮查询上限失效）", not _bad_names, ",".join(_bad_names))

print("== 工具方法是否都存在 ==")
cls = T.Tools
bad = []
for name, _args, _desc, method in T.spec_list({k: True for k in (
        "send", "sticker", "recent", "members", "memory", "stickers_list", "collect",
        "vision", "sticker_view", "sticker_note", "wake", "poke", "search", "skills",
        "memory_write", "finish")}):
    if not callable(getattr(cls, method, None)):
        bad.append("%s→%s" % (name, method))
ck("工具清单里的方法都在", not bad, "、".join(bad))

print()
if FAIL:
    print("有问题：%s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
