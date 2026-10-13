"""离线验证：推面板的记账（mark_pushed 落盘 / push_pending 挑未推的 / reconcile_pushed 回填）。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/sticker_push_sim.py
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import stickers as S          # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


_tmp = tempfile.mkdtemp(prefix="stkpush_")
_old = (S.INDEX, S.DATA)
S.DATA = _tmp
S.INDEX = os.path.join(_tmp, "index.json")


def _mk(i, name):
    p = os.path.join(_tmp, name)
    with open(p, "wb") as f:
        f.write(b"x" * (10 + i))
    h = hashlib.md5(open(p, "rb").read()).hexdigest()
    return {"id": "s%d" % i, "file": name, "hash": h, "desc": "第%d张" % i,
            "tags": [], "group": "869622030", "used": 0, "size": 10 + i}


try:
    S.save([_mk(1, "a.jpg"), _mk(2, "b.gif"), _mk(3, "c.png")])
    print("== 基础 ==")
    ck("初始都没有 face_pushed", all(not x.get("face_pushed") for x in S.load()))

    print("== mark_pushed 要落盘 ==")
    ck("mark_pushed 返回 True", S.mark_pushed("s1") is True)
    ck("重新 load 后仍然是已推（真的落盘了）",
       [x for x in S.load() if x["id"] == "s1"][0].get("face_pushed") is True)

    print("== push_pending 挑的是『没推过的』 ==")
    _sent = []
    S.add_face = lambda p, uin="": (_sent.append(os.path.basename(p)), True)[1]
    _n = S.push_pending(limit=10)
    ck("补推了剩下 2 张", _n == 2, str(_sent))
    ck("推的是 b/c 而不是 a（a 已推）", sorted(_sent) == ["b.gif", "c.png"], str(_sent))
    ck("推完盘上都是已推", all(x.get("face_pushed") for x in S.load()))

    print("== reconcile_pushed：面板里有的直接回填 ==")
    _items = S.load()
    for x in _items:
        x["face_pushed"] = False
    S.save(_items)
    _md5 = hashlib.md5(open(os.path.join(_tmp, "b.gif"), "rb").read()).hexdigest()
    S.fetch_face_detail = lambda uin="", count=400: [{"emoji_id": "e1", "md5": _md5,
                                                      "url": "", "desc": ""}]
    _r = S.reconcile_pushed()
    _cur = {x["id"]: bool(x.get("face_pushed")) for x in S.load()}
    ck("只回填命中 md5 的那张", _r.get("marked") == 1 and _cur["s2"] is True, str(_r))
    ck("没命中的不动", _cur["s1"] is False and _cur["s3"] is False, str(_cur))

    print("== 接线：收图成功后要落盘 ==")
    _src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    ck("collect 路径调用 mark_pushed", "stickers.mark_pushed" in _src)
    _ssrc = open(os.path.join(ROOT, "stickers.py"), encoding="utf-8").read()
    ck("push_pending 先筛未推再取 limit",
       "pend = [x for x in items" in _ssrc and "items[: int(limit or 80)]" not in _ssrc)
    ck("mirror 里先回填再补推", "reconcile_pushed()" in _ssrc and "回填已推标志" in _ssrc)
finally:
    S.INDEX, S.DATA = _old
    shutil.rmtree(_tmp, ignore_errors=True)

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
