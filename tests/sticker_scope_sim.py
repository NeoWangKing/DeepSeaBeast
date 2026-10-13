"""离线验证：按群限定可发风格（Denial 只发可爱/形象那批）。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/sticker_scope_sim.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import stickers as S          # noqa: E402
import main                   # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
o = main.QqPeakGate.__new__(main.QqPeakGate)
o.cfg = cfg
o._nick = ""
o._log = lambda *a, **k: None
o._log_debug = lambda *a, **k: None

print("== 配置 ==")
sc = cfg.get("stickers") or {}
ck("Denial 群配了 id 白名单", len((sc.get("allow_ids_by_group") or {}).get("1105410423") or []) >= 5)
ck("Denial 群配了风格标签", len((sc.get("allow_tags_by_group") or {}).get("1105410423") or []) >= 5)
ck("Denial 群已从 send_exclude_groups 移除", "1105410423" not in (sc.get("send_exclude_groups") or []))
ck("豹群仍然不发表情", "966812151" in (sc.get("send_exclude_groups") or []))

print("== 判定：Denial 只放可爱/形象 ==")
_lst = [x for x in (S.load() or []) if x.get("id")]
_ok = [x for x in _lst if o._sticker_ok("1105410423", x)]
_no = [x for x in _lst if not o._sticker_ok("1105410423", x)]
ck("可发是「一批」而不是全部", 8 <= len(_ok) <= 70, "可发 %d / 共 %d" % (len(_ok), len(_lst)))
_ids_ok = {str(x["id"]) for x in _ok}
_prefer = [str(x) for x in (sc.get("prefer_ids") or [])]
ck("她自己的形象图（prefer_ids）都能发", all(i in _ids_ok for i in _prefer), str(_prefer[:3]))
ck("拇指向上这类不属于可爱系 → 不能发", "s1790876947821" not in _ids_ok)
ck("可爱的猫猫 → 能发", "s1790919038508" in _ids_ok)
ck("可发的都有可爱/形象标签", all(
    any(t in (str(x.get("desc") or "") + " ".join(x.get("tags") or []))
        for t in (sc["allow_tags_by_group"]["1105410423"])) or str(x["id"]) in _prefer
    for x in _ok))

print("== 其它群不受影响 ==")
ck("闪电网：全部都能发",
   len([x for x in _lst if o._sticker_ok("869622030", x)]) == len(_lst))
ck("没配的群 = 不限制（默认放行）", o._sticker_ok("123456789", _no[0]) is True)

print("== 提示词：清单只列可发的 ==")
_menu = o._sticker_menu_text("1105410423") or ""
ck("清单非空", bool(_menu))
ck("清单里说明了只发可爱/萌系", "只发**可爱/萌系**" in _menu)
ck("清单不列拇指向上那张", "拇指向上" not in _menu)
ck("清单列出她形象图（喝奶茶等）", "喝奶茶" in _menu)

print("== 工具可用性 ==")
_specs = [s[0] for s in o._agent_specs_for("1105410423")]
ck("Denial 有 send_sticker", "send_sticker" in _specs)
ck("豹群没有 send_sticker", "send_sticker" not in [s[0] for s in o._agent_specs_for("966812151")])

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
