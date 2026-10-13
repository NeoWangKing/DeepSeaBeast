"""离线验证：Denial 群发表情不再报错、被禁的会换一张、mood 只在允许的池子里挑。"""
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import main                   # noqa: E402
import stickers as S          # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
GID = "1105410423"


async def _run():
    o = main.QqPeakGate.__new__(main.QqPeakGate)
    o.cfg = cfg
    o._log = lambda *a, **k: None
    o._log_debug = lambda *a, **k: None
    o._nick = ""
    got = []

    async def _send(target, it, reply_to_id=""):
        got.append(it)
    o._agent_send_sticker = _send
    cb = o._agent_callbacks({"gid": GID, "uid": "u1",
                             "umo": "qq:GroupMessage:%s" % GID, "event": None})

    # ① 点名一张被禁的（拇指向上）→ 应该自动换一张允许的，而不是报错
    r1 = cb["send_sticker"]("s1790876947821")
    await asyncio.sleep(0.15)
    ck("点名被禁的那张：不报错、返回成功", r1 is True, str(r1)[:40])
    ck("…自动换成允许的（可爱系）", bool(got) and o._sticker_ok(GID, got[0]),
       str([x.get("id") for x in got]))

    # ② mood 挑图：只在允许的池子里挑（以前会挑到被禁的然后落空）
    got.clear()
    r2 = cb["send_sticker"]("", "可爱")
    await asyncio.sleep(0.15)
    ck("mood=可爱 能挑到（返回成功）", r2 is True, str(r2)[:40])
    ck("…挑中的是允许的那批", bool(got) and o._sticker_ok(GID, got[0]),
       str([(x.get("id"), (x.get("desc") or "")[:10]) for x in got]))

    # ③ 允许的那张直接点名 → 正常发出
    got.clear()
    r3 = cb["send_sticker"]("s1791463279837")       # 她形象那张
    await asyncio.sleep(0.15)
    ck("点名允许的（形象图）：正常发出", bool(got) and got[0]["id"] == "s1791463279837" and r3 is True,
       str(r3)[:40])


asyncio.run(_run())

print("== pick(pool=...) 直接验证 ==")
_pool = [x for x in (S.load() or []) if main.QqPeakGate._sticker_ok.__get__(
    type("X", (), {"cfg": cfg, "_sticker_ok": main.QqPeakGate._sticker_ok})())] if False else None
_o = main.QqPeakGate.__new__(main.QqPeakGate)
_o.cfg = cfg
_allowed = [x for x in (S.load() or []) if _o._sticker_ok(GID, x)]
_c = S.pick("可爱", limit=6, pool=_allowed)
ck("pool 里的结果都属于允许的那批", all(_o._sticker_ok(GID, x) for x in _c), str(len(_c)))
ck("pool 为空时 pick 返回空（不会越界）", S.pick("可爱", limit=6, pool=[]) == [])

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
