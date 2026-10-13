"""离线验证：她的 DeepSeek 笑话库（多条 + 轮换规则，不再只有 V我50）。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/deepseek_joke_sim.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import promptlib                        # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
patch = open(os.path.join(ROOT, "prompts", "personas", "_common_patch.txt"), encoding="utf-8").read()
card = open(os.path.join(ROOT, "prompts", "system_prompt_friend.txt"), encoding="utf-8").read()


def build(chat_key):
    t, _ = promptlib.build_system_prompt(plugin_dir=ROOT, cfg=cfg, chat_key=chat_key,
                                         private=False,
                                         caps={"vision": True, "search": True, "kb": True})
    return t


print("== 笑话库本体 ==")
_jokes = re.findall(r"(?m)^\d+\. ([^\n]+)", patch)
ck("库里至少 10 条笑话", len(_jokes) >= 10, "共 %d 条" % len(_jokes))
ck("V我50 只是其中一条", any("V我50" in j for j in _jokes))
_others = [j for j in _jokes if "V我50" not in j]
ck("除 V我50 外还有 ≥9 条", len(_others) >= 9, str(len(_others)))
ck("写了『别重复/轮着用』的规矩", "轮着用" in patch and "不许每次都用 V我50" in patch)
ck("写明『最近 20 条说过就别再说』", "最近 20 条" in patch)
ck("写明『再讲一个必须换不同的』", "必须换**不同的**梗" in patch)

print("== 每个群都能看到（通用补丁） ==")
for gid, name in (("869622030", "闪电群"), ("1105410423", "Denial"), ("966812151", "豹群"),
                  ("p:287383512", "私聊")):
    t = build(gid)
    ck("%s：提示词里有笑话库" % name, "【DeepSeek 笑话库" in t)
    ck("%s：库里有 ≥10 条" % name, len(re.findall(r"(?m)^\d+\. ", t)) >= 10,
       str(len(re.findall(r"(?m)^\d+\. ", t))))

print("== 不再只写死 V我50 ==")
ck("闪电群卡：被问 AI 时改成『挑一句最近没说过的』",
   "别每次都 V我50" in card or "挑一句最近没说过的" in card)
ck("闪电群卡：复读规则里点名了 V我50", "V我50" in card and "别复读固定台词" in card)
ck("示例里标注了『只是一个例子，实际轮着换』", "实际轮着换" in patch or "别当口头禅" in card)
_ai_line = [l for l in card.splitlines() if "被问是不是 AI" in l]
ck("被问 AI 的那句不再直接给固定台词",
   bool(_ai_line) and "V我50解锁下一句" not in _ai_line[0], str(_ai_line)[:80])

print("== KB 里也有一份（可检索） ==")
_kb = os.path.join(ROOT, "data", "kb", "deepseek-jokes.md")
ck("KB 文件存在", os.path.isfile(_kb))
_idx = json.load(open(os.path.join(ROOT, "data", "kb", "_index.json"), encoding="utf-8"))
_srcs = {str(c.get("source")) for c in (_idx.get("chunks") or [])}
ck("已建进索引", any("deepseek-jokes" in s for s in _srcs), str(sorted(_srcs))[:100])

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
