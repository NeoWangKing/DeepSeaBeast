"""离线验证：群印象不能把她自己写成"群里另一个机器人"。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/memory_self_sim.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


_cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))

print("== 配置：她的别名 ==")
alias = (_cfg.get("memory") or {}).get("self_aliases") or []
ck("memory.self_aliases 配了", len(alias) >= 3, "、".join(alias))
for _n in ("大肥鱼", "蓝色邪恶大肥鱼", "DeepSeaBeast"):
    ck("别名里有 %s" % _n, _n in alias)

print("== 生成群印象的提示词：说清那是她自己 ==")
from memory import prompts                  # noqa: E402
_p = prompts.group_prompt("Neo武神: 我还得调她\nNa1ky0: 问肥鱼一道数学题会吃很多token吗",
                          limit=200, self_names=alias)
ck("提示词点名『就是你自己』", "就是指**你自己**" in _p)
ck("提示词禁止写成『别的机器人』", "别的机器人" in _p)
ck("提示词里带上了她的别名", "大肥鱼" in _p)
ck("self_names 为空也不炸", "本机器人的名字" in prompts.group_prompt("x", 100))

print("== 注入给她时再提醒一次 ==")
import main                                 # noqa: E402
o = main.QqPeakGate.__new__(main.QqPeakGate)
o.cfg = _cfg
o._log = lambda *a, **k: None
o._log_debug = lambda *a, **k: None
_mb = o._memory_block("869622030", "3245938285", "", "")
ck("记忆块非空", bool(_mb))
ck("说明了那些名字都是她自己", "都是**你自己**" in _mb, _mb[:120])
ck("明确『不要理解成群里还有个别的机器人』", "不要理解成" in _mb)

print("== 现场群印象文本 ==")
from memory import store                    # noqa: E402
_txt = store.read_profile("869622030")
ck("不再出现『还有一个叫大肥鱼的AI机器人』",
   "还有一个叫" not in _txt and "AI机器人，群主在调试它" not in _txt)
ck("没有把她写成第三方机器人",
   "还有一个叫" not in _txt and "AI机器人，群主在调试它" not in _txt)
ck("手写区点名『那个机器人/它』也是她自己", "那个机器人" in _txt)
ck("注入文本仍带自我提醒（自动区被重写也兜得住）", "都是**你自己**" in o._memory_block(
    "869622030", "3245938285", "", ""))
_raw_live = open(store.profile_path("869622030"), encoding="utf-8").read()
ck("手写区有永久提醒", ("就是**你自己**" in _raw_live or "都是**你自己**" in _raw_live)
   and "## 手写补充" in _raw_live)
ck("注入文本里带上了手写提醒", "不是别的机器人" in _txt)

print("== 自动区重建不会丢掉手写提醒 ==")
_tmp = "999_test_gid"
try:
    store.write_profile(_tmp, "① 测试正文", "meta")
    _p2 = store.profile_path(_tmp)
    with open(_p2, encoding="utf-8") as f:
        _raw = f.read()
    _raw = _raw.replace("（可在这里写死规矩", "- 手写规矩：别把自己写成第三方\n（可在这里写死规矩")
    with open(_p2, "w", encoding="utf-8") as f:
        f.write(_raw)
    store.write_profile(_tmp, "① 重建后的正文", "meta2")     # 模拟下一次自动重建
    _after = store.read_profile(_tmp)
    ck("重建后新正文在", "重建后的正文" in _after)
    ck("重建后手写规矩还在", "别把自己写成第三方" in _after)
finally:
    try:
        os.remove(store.profile_path(_tmp))
    except Exception:
        pass

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
