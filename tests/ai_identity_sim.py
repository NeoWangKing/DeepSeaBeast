"""离线验证：技术助手人格必须承认自己是 AI；活泼群友人设不受影响。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/ai_identity_sim.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import promptlib                       # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))


def build(chat_key):
    t, m = promptlib.build_system_prompt(plugin_dir=ROOT, cfg=cfg, chat_key=chat_key,
                                         private=False,
                                         caps={"vision": True, "search": True, "kb": True})
    return t, m


print("== 配置 ==")
_honest = ((cfg.get("prompt") or {}).get("ai_honest_personas") or [])
ck("配了 ai_honest_personas", "project_assistant.txt" in _honest, str(_honest))

print("== Denial 群（技术助手）：要承认是 AI ==")
_t, _m = build("1105410423")
ck("人格卡是技术助手", "project_assistant" in str(_m.get("persona")), str(_m.get("persona")))
ck("提示词里有『你是 AI，别装群友』", "你是 AI，别装群友" in _t)
ck("要求『直接承认』", "直接承认" in _t)
ck("不许装群友/否认", "别装群友" in _t and "耍赖否认" in _t)
ck("人格卡里写死了『你是 AI』", "你是 **AI**（灵其啊写的技术助手）" in _t)
ck("不再出现『不自称 AI』那套", "不自称 AI" not in _t)
ck("仍然保持谦虚（Synthia 优先）", "Synthia" in _t)

print("== 闪电群（群友人设）：保持原样 ==")
_t2, _m2 = build("869622030")
ck("群友人设不带『别装群友』", "你是 AI，别装群友" not in _t2)
ck("原『不自称 AI』还在", "不自称 AI" in _t2)

print("== _ai_honest 的匹配（带来源前缀也能认） ==")
from promptlib import sections as SEC          # noqa: E402
ck("prompt_by_group:project_assistant.txt → True",
   SEC._ai_honest({"cfg": cfg, "persona": "prompt_by_group:project_assistant.txt"}) is True)
ck("system_prompt_friend.txt → False",
   SEC._ai_honest({"cfg": cfg, "persona": "prompt_by_group:system_prompt_friend.txt"}) is False)
ck("没配 persona → False", SEC._ai_honest({"cfg": cfg, "persona": ""}) is False)

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
