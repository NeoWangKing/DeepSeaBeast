"""离线验证：技术助手人格 + 按人格禁用段落 + 资料库分域 + 只记 @ 的群。

跑法：python3 tests/tech_persona_sim.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import kb as KB                        # noqa: E402
import promptlib                       # noqa: E402
from promptlib import persona as P     # noqa: E402
from promptlib import sections as SEC  # noqa: E402
from promptlib import SECTIONS         # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


print("== 人格卡在位 ==")
_p = P.resolve(ROOT, "project_assistant")
ck("project_assistant 能找到", bool(_p), _p)
_txt = open(_p, encoding="utf-8").read() if _p else ""
ck("写了「只有被 @ 才回」", "只有被 @" in _txt)
ck("写了严谨/来源要求", "出处" in _txt and "不确定就直说" in _txt)
ck("写了不玩梗/不用表情包", "不玩梗" in _txt and "不用表情包" in _txt)
ck("写了资料库先翻仓库", "先翻资料" in _txt)
cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))

print("== 按人格禁用行为段 ==")
_ctx = {"plugin_dir": ROOT, "prompts_dir": ROOT, "cfg": cfg, "persona": "personas/project_assistant.txt",
        "caps": {"tools_text": "- send_message：发言", "tools_send": True},
        "bot_name": "小鲸鱼", "participation": "normal", "sticker_level": 3}
_off = SEC.build_behavior(dict(_ctx, cfg={**cfg, "prompt": {**cfg.get("prompt", {}),
                                                            "disable_sections_by_persona": {"project_assistant.txt": ["sticker_rules"]}}}))
_onn = SEC.build_behavior(_ctx)
ck("关掉表情段后，行为层里没有表情包规矩", "表情包：像真人一样用" not in _off)
ck("不关时是有的", "表情包：像真人一样用" in _onn)
_sec_ids = [s for s, _f in SECTIONS]
ck("段落 id 里确实有 sticker_rules", "sticker_rules" in _sec_ids)

print("== 资料库分域 ==")
tmp = tempfile.mkdtemp(prefix="kbsc-")
try:
    _save_data, _save_idx = KB.DATA, KB.INDEX
    KB.DATA = os.path.join(tmp, "kb")
    KB.INDEX = os.path.join(KB.DATA, "_index.json")
    os.makedirs(os.path.join(KB.DATA, "projA"), exist_ok=True)
    open(os.path.join(KB.DATA, "projA", "_scope.json"), "w", encoding="utf-8").write('{"name":"projA"}')
    open(os.path.join(KB.DATA, "projA", "README.md"), "w", encoding="utf-8").write(
        "# projA\n\n这个项目用紫色鲸鱼协议做鉴权，入口在 auth/verify.py。\n")
    open(os.path.join(KB.DATA, "projA", "verify.py"), "w", encoding="utf-8").write(
        "def verify_token(t):\n    # 紫色鲸鱼协议：校验签名\n    return bool(t)\n")
    open(os.path.join(KB.DATA, "default.md"), "w", encoding="utf-8").write(
        "# 默认域\n\n这里讲的是别的东西：明日方舟抽卡概率。\n")
    r = KB.build("projA", verbose=False)
    KB.build("", verbose=False)
    ck("子域建索引成功", bool(r), r)
    _h1 = KB.search("紫色鲸鱼协议 鉴权", 3, 0.02, scope="projA")
    _h2 = KB.search("紫色鲸鱼协议 鉴权", 3, 0.02)
    ck("子域能搜到仓库内容", any("verify.py" in str(h.get("source")) or "README" in str(h.get("source"))
                              for h in _h1), [h.get("source") for h in _h1])
    ck("默认域搜不到子域内容（隔离）", not any("projA" in str(h.get("source")) for h in _h2),
       [h.get("source") for h in _h2])
    ck("默认域自己的文件还在", any("default.md" in str(h.get("source")) for h in
                                KB.search("抽卡概率", 3, 0.02)))
    ck("代码文件也被索引（_files 认得 .py）", any(f.endswith("verify.py") for f in KB._files("projA")))
    ck("默认域不把子域算进来", not any("projA" in f for f in KB._files("")))
finally:
    KB.DATA, KB.INDEX = _save_data, _save_idx
    shutil.rmtree(tmp, ignore_errors=True)

print("== 仓库导入工具在位 ==")
_t = open(os.path.join(ROOT, "tools", "kb_import_repo.py"), encoding="utf-8").read()
ck("有 clone/拷贝与 _scope.json", "git" in _t and "_scope.json" in _t and "build(scope" in _t)
ck("跳过二进制/大文件", "_texty" in _t and "MAX_FILE" in _t)

print("== main.py 接线 ==")
_m = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("有 _kb_scope（按群/按人格取域）", "def _kb_scope(" in _m)
ck("两处检索都带 scope", _m.count("scope=_scope_kb") >= 1 and "scope=_scope_kb2" in _m)
ck("有 record_at_only_groups（只记 @ 的群）", "record_at_only_groups" in _m)
kc = cfg.get("kb") or {}
ck("config 预留 scope_by_group / scope_by_persona",
   "scope_by_group" in kc and "scope_by_persona" in kc)
ck("config 预留 record_at_only_groups", "record_at_only_groups" in cfg)
ck("config 预留 disable_sections_by_persona",
   "disable_sections_by_persona" in (cfg.get("prompt") or {}))

print()
if FAIL:
    print("有问题：%s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
