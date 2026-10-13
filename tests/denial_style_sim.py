"""离线验证：Denial 群说话风格向 Synthia 学"温度"但不学"篇幅"。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/denial_style_sim.py
"""
import json
import os
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
card = open(os.path.join(ROOT, "prompts", "personas", "project_assistant.txt"),
            encoding="utf-8").read()
txt, meta = promptlib.build_system_prompt(
    plugin_dir=ROOT, cfg=cfg, chat_key="1105410423", private=False,
    caps={"vision": True, "search": True, "kb": True})

print("== 人格卡：学 Synthia 的温度 ==")
ck("有『学 Synthia 的那点温度』这一节", "学 Synthia 的那点温度" in card)
ck("要点明『先接住对方的情绪』", "先接住对方的情绪" in card)
ck("要求表达真诚兴趣/好奇", "真诚的兴趣和好奇" in card)
ck("情绪要具体（不要空泛客套）", "情绪要**具体**" in card or "情绪要具体" in card)
ck("不夹英文句子（区别于 Synthia）", "不用夹英文句子" in card)

print("== 但明确不许长篇 ==")
ck("不再限死：明确『这个群允许长回答』", "这个群允许长回答" in card)
ck("不管长短都不许加粗/小标题/清单", "加粗/小标题/清单" in card)
ck("不给『首先/其次/总结一下』", "首先/其次/总结一下" in card)
ck("默认短（≤40 字）", "≤40 字" in card)
ck("允许『真需要展开时就展开』", "真需要展开时就展开" in card)
ck("长的写法：先结论 + 2~4 条要点", "先一句结论" in card and "2~4 条" in card)
ck("还是要问『要我细说吗』（不确定时）", "要我细说吗" in card)
ck("不要用换行分段（想分就发两条）", "不要用换行分段" in card)

print("== 矛盾的旧规矩已清掉 ==")
ck("不再写『这个群不发任何表情包』", "不发任何表情包" not in card)
ck("表情规矩与放开发图一致", "可以**在合适的时机回一张可爱的" in card or "偶尔配一张" in card)

print("== 组装后的提示词也带上这些 ==")
ck("提示词里有学 Synthia 那节", "学 Synthia 的那点温度" in txt)
ck("提示词里有篇幅上限", "≤40 字" in txt)
ck("技术助手仍是『承认是 AI』", "你是 AI，别装群友" in txt)
ck("仍然谦虚（Synthia 是正主）", "Synthia" in txt and "正主" in txt)

print("== 工具发言按换行拆条（配套） ==")
from agent import tools as T            # noqa: E402
sent = []
_t = T.Tools("1105410423", {"send_text": lambda x, **k: sent.append(x)}, {"max_calls": 5})
_t.send_message("第一段\n\n第二段 ||| 第三段")
ck("换行与 ||| 都拆成独立消息", sent == ["第一段", "第二段", "第三段"], str(sent))


print("== 工具层：长回答不丢内容 / 短群仍有限制 ==")
from agent import tools as T            # noqa: E402

_long = "\n".join("第%d段：这里是一段排查说明，写得长一点。" % i for i in range(1, 13))
_den_cfg = {"max_text_chars": 1500, "max_parts": 4}
sent2 = []
_t4 = T.Tools("1105410423", {"send_text": lambda x, **k: sent2.append(x)}, _den_cfg)
_r4 = _t4.send_message(_long)
ck("Denial 群：长文能发出去", sent2 and "已发" in _r4, _r4[:40])
ck("…拆成不超过 4 条", len(sent2) <= 4, str(len(sent2)))
_flat = "".join(sent2).replace("\n", "")
ck("…内容没有被截断（12 段全在）",
   all(("第%d段" % i) in _flat for i in range(1, 13)), str(_flat[:60]))
sent3 = []
_t5 = T.Tools("869622030", {"send_text": lambda x, **k: sent3.append(x)}, {})   # 默认 400 字
_r5 = _t5.send_message("长" * 900)
ck("默认群：超 400 字会被拦住（并给出建议）", (not sent3) and "太长" in _r5, _r5[:50])

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
