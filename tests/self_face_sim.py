"""离线验证：她自己那组「形象」表情更容易被挑中，且常驻表情清单。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/self_face_sim.py
（纯离线部分用 python3 也能跑。）
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import stickers as S          # noqa: E402

FAIL = []
PREFER = ["s1791462696207", "s1791462846379", "s1791463274545",
          "s1791463275943", "s1791463279025", "s1791463279754", "s1791463279837"]


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


print("== 配置 ==")
pf = S.prefer_cfg()
ck("prefer_ids 读到 7 个", len(pf["ids"]) == 7, str(pf["ids"])[:70])
ck("prefer_ids 就是她那组形象", sorted(pf["ids"]) == sorted(PREFER))
ck("prefer_boost 默认 3", float(pf["boost"]) == 3.0, str(pf["boost"]))
_real = {str(x.get("id")): x for x in (S.load() or [])}
ck("这 7 张都在库里", all(i in _real for i in pf["ids"]))
ck("这 7 张都有备注（清单里看得懂）",
   all(str(_real[i].get("desc") or "").strip() for i in pf["ids"] if i in _real))

print("== pick：形象图更容易进候选（但情绪对得上的仍排前面） ==")
_now = int(time.time())
FAKE = [{"id": PREFER[6], "desc": "大肥鱼的形象", "tags": ["形象"], "used": 9, "last_used": 0},
        {"id": "x1", "desc": "普通杂图", "tags": ["杂图"], "used": 1, "last_used": 0},
        {"id": "x2", "desc": "无语脸", "tags": ["无语", "离谱", "嫌弃"], "used": 1, "last_used": 0},
        {"id": "x3", "desc": "刚发过", "tags": ["无语"], "used": 1, "last_used": _now - 300}]
_o = S.load
S.load = lambda: FAKE
try:
    got = [x["id"] for x in S.pick("今天真冷")]
    ck("没标签命中时，形象图仍进候选", got == [PREFER[6]], str(got))
    ck("没标签命中时，普通杂图不进来", "x1" not in got)
    got = [x["id"] for x in S.pick("无语 离谱 嫌弃")]
    ck("情绪对得上的排形象图前面", got[:2] == ["x2", PREFER[6]], str(got))
    ck("最近用过的照旧被压下去", "x3" not in got, str(got))
    got = [x["id"] for x in S.pick("今天真冷", prefer=[])]
    ck("prefer=[] 时不加分（可关）", got == [], str(got))
    got = [x["id"] for x in S.pick("今天真冷", prefer_boost=0)]
    ck("prefer_boost=0 时不加分", got == [], str(got))
    got = [x["id"] for x in S.pick("今天真冷", limit=1)]
    ck("limit 仍然生效", len(got) == 1, str(got))
    S.load = lambda: [dict(FAKE[0], last_used=_now - 600)]
    got = [x["id"] for x in S.pick("今天真冷")]
    ck("形象图刚发过也会被压（不刷同一张）", got == [], str(got))
finally:
    S.load = _o

print("== 表情清单：形象图常驻 ==")
try:
    import main
except Exception as e:
    print("  （跳过：%s）" % str(e)[:70])
else:
    o = main.QqPeakGate.__new__(main.QqPeakGate)
    o.cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    o._log_debug = lambda *a, **k: None
    fake = []
    for i in range(40):
        if i < 7:
            fake.append({"id": PREFER[i], "desc": "形象图%d" % i, "tags": ["形象"],
                         "used": 90, "last_used": 0})       # 用得多 → 旧逻辑会把它挤出清单
        else:
            fake.append({"id": "f%03d" % i, "desc": "杂图%d" % i, "tags": ["杂图"],
                         "used": 1, "last_used": 0})
    S.load = lambda: fake
    try:
        txt = o._sticker_menu_text("869622030")
        ck("清单非空", bool(txt))
        ck("7 张形象图全在清单里", all(i in txt for i in PREFER),
           str([i for i in PREFER if i not in txt]))
        lines = [l for l in (txt or "").split("\n") if l.startswith("- ")]
        ck("清单按 prompt_max 给 20 张", len(lines) == 20, str(len(lines)))
        pos = {i: next((k for k, l in enumerate(lines) if i in l), 99) for i in PREFER}
        ck("形象图排在最前面", max(pos.values()) < min(
            [k for k, l in enumerate(lines) if not any(i in l for i in PREFER)] or [99]),
           str(sorted(pos.values())))
        ck("清单里说明了按情绪挑", "send_sticker(mood" in (txt or ""))
        ck("豹群照样不发清单", o._sticker_menu_text("966812151") == "")
    finally:
        S.load = _o

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
