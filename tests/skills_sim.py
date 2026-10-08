"""离线验证：技能层（SKILL.md 索引 + use_skill 按需展开）。

跑法：python3 tests/skills_sim.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from promptlib import skills as SK       # noqa: E402
from promptlib import sections as SEC    # noqa: E402
from agent import tools as T             # noqa: E402
from agent import loop as L              # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


print("== frontmatter 解析 ==")
meta, body = SK.parse_frontmatter("---\nname: a-b\ndescription: 一句话\n---\n正文在此\n")
ck("name 解析", meta.get("name") == "a-b")
ck("description 解析", meta.get("description") == "一句话")
ck("正文干净", body == "正文在此")
meta2, body2 = SK.parse_frontmatter("没有 frontmatter 的纯文本")
ck("没有 frontmatter 也能用", meta2 == {} and body2 == "没有 frontmatter 的纯文本")

print("== 扫描 / 索引 / 加载（临时目录） ==")
tmp = tempfile.mkdtemp(prefix="skillsim-")
try:
    os.makedirs(os.path.join(tmp, "skills", "demo"))
    with open(os.path.join(tmp, "skills", "demo", "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: demo\ndescription: 演示技能\ntriggers: 演示, demo\n---\n这是正文\n第二行\n")
    with open(os.path.join(tmp, "skills", "flat.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: flat\ndescription: 单文件技能\n---\n平铺正文\n")
    with open(os.path.join(tmp, "skills", "bad.md"), "w", encoding="utf-8") as f:
        f.write("没有 frontmatter，名字里有中文/符号，应该被忽略")
    got = [x["name"] for x in SK.discover(tmp, ttl=0)]
    ck("发现两个合法技能", set(got) == {"demo", "flat"}, str(got))
    idx = SK.index_text(tmp)
    ck("索引含名字和一句话", "demo：演示技能" in idx and "flat：单文件技能" in idx)
    ck("索引含触发词", "触发：演示, demo" in idx)
    ck("索引不含正文", "这是正文" not in idx)
    ck("load 拿到全文", SK.load(tmp, "demo").startswith("这是正文"))
    ck("load 单文件技能", SK.load(tmp, "flat") == "平铺正文")
    ck("load 不存在的技能 → 空", SK.load(tmp, "nope") == "")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("== 仓库里的真技能 ==")
names = SK.names(ROOT)
ck("有 turtle_soup 技能", "turtle_soup" in names, str(names))
ck("有 verify 技能", "verify" in names)
ck("有 official_sites 技能（二游官网清单）", "official_sites" in names, str(names))
_off = SK.load(ROOT, "official_sites")
ck("官网清单含明日方舟/终末地/原神", all(u in _off for u in ("ak.hypergryph.com", "endfield.hypergryph.com", "yuanshen.com")))
ck("官网清单强调先看官网公告", "先查对应官网" in _off or "先查对应官网" in _off)
ck("官网清单提醒别混淆本家/终末地", "别把「终末地」和「本家明日方舟」搞混" in _off)
ck("海龟汤说明书提到怎么开局", "来一局海龟汤" in SK.load(ROOT, "turtle_soup"))
ck("求证说明书提到先搜后读原文", "read_url" in SK.load(ROOT, "verify"))

print("== 提示词段 ==")
sec_txt = SEC._skills_index({"plugin_dir": ROOT, "caps": {"tools_text": "- send_message：发言"}})
ck("有工具时注入技能索引", "【技能（按需展开" in sec_txt and "use_skill" in sec_txt)
ck("没工具时不注入", SEC._skills_index({"plugin_dir": ROOT, "caps": {}}) == "")

print("== 工具层 ==")
got = []
t = T.Tools("869622030", {"use_skill": lambda n: (got.append(n) or "【技能说明书：demo】\n正文")}, {})
ck("use_skill 正常返回", "技能说明书" in t.use_skill("demo"))
ck("技能名透传", got == ["demo"])
ck("记进 skills_used（审计）", t.skills_used == ["demo"])
ck("没有这个技能时不炸", "没有这个技能" in T.Tools("g", {"use_skill": lambda n: ""}, {}).use_skill("x"))
ck("回调缺失时不炸", T.Tools("g", {}, {}).use_skill("x") == "没有技能库")
ck("技能名空也给提示", "没有这个技能" in T.Tools("g", {"use_skill": lambda n: ""}, {}).use_skill(""))

print("== schema ==")
on = L.spec_to_openai([s for s in T.spec_list({"skills": True}) if s[0] == "use_skill"])
ck("skills 开着时注册 use_skill", len(on) == 1)
ck("use_skill.name 不是必填（空名=看索引）", on[0]["function"]["parameters"]["required"] == [])
ck("skills 关着时不注册", "use_skill" not in [s[0] for s in T.spec_list({"skills": False})])

print()
if FAIL:
    print("有问题：%s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
