"""离线验证：调侃类表情的"用法"要能被她读到（donk 梗图事故）。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/meme_reading_sim.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import stickers as S          # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


print("== 那张 donk 梗图的备注 ==")
_it = S.find("s1791555658902") or {}
_desc = str(_it.get("desc") or "")
ck("备注写清了用法（调侃发挥一般）", "调侃" in _desc and ("发挥一般" in _desc or "拉胯" in _desc), _desc[:40])
ck("不是只写画面（没有『快哭了』这种）", "快哭了" not in _desc)
ck("打上了便于选图的标签", "donk" in (_it.get("tags") or []))

print("== KB 里有『调侃 ≠ 安慰』这一课 ==")
_kb = open(os.path.join(ROOT, "data", "kb", "cs2-memes.md"), encoding="utf-8").read()
ck("写了 donk 梗图的用法", "donk 打游戏梗图" in _kb)
ck("点明不是『他输了在哭』", "不是" in _kb and "哭" in _kb)
ck("写了调侃对象常常不是发图的人/她", "不是发图的人" in _kb or "不是你" in _kb)
ck("给了拿不准时的做法（短接或问）", "这啥" in _kb)
_idx = json.load(open(os.path.join(ROOT, "data", "kb", "_index.json"), encoding="utf-8"))
_srcs = {str(c.get("source")) for c in (_idx.get("chunks") or [])}
ck("重建过索引（cs2-memes 在索引里）", any("cs2-memes" in s for s in _srcs), str(sorted(_srcs))[:120])

print("== 人格卡 / 看图提示 ==")
_card = open(os.path.join(ROOT, "prompts", "system_prompt_friend.txt"), encoding="utf-8").read()
ck("人格卡：梗图先看『用法』", "表情包/梗图先看" in _card and "调侃" in _card)
ck("人格卡：别按画面字面理解", "别按画面字面理解" in _card)
_msrc = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("看图提示：问『在调侃谁』", "在调侃谁" in _msrc)
ck("看图提示：认得就写是谁、不认得别猜", "认得图里的人是谁就写名字，不认得别猜" in _msrc)


print("== 拇指向上 = 肯定，不是敷衍 ==")
_ts = S.find("s1790876947821") or {}
_td = str(_ts.get("desc") or "")
ck("备注写明『肯定/牛逼』", "肯定" in _td and ("牛逼" in _td or "夸" in _td), _td[:46])
ck("备注明确『不是敷衍』", "不是敷衍" in _td or "不是" in _td)
ck("标签里没有『敷衍』这种误读", "敷衍" not in (_ts.get("tags") or []), str(_ts.get("tags")))
ck("KB 也写了这一条", "拇指向上（简笔画笑脸）" in
   open(os.path.join(ROOT, "data", "kb", "cs2-memes.md"), encoding="utf-8").read())
_msrc2 = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("看图提示：态度别按画风猜", "态度别按画风猜" in _msrc2)

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
