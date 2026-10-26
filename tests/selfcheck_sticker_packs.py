#!/usr/bin/env python3
"""贴图按人格分包：收集打上当前人格的包，发送优先本卡自己的图。"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


def load_plugin(data_dir: str):
    os.environ["DEEPSEA_PLUGIN_DATA"] = data_dir
    spec = importlib.util.spec_from_file_location("stickers", "plugins/stickers/main.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    td = tempfile.mkdtemp()
    # 造两套卡目录，各有几张图
    cards = Path(td) / "cards"
    for card, files in (("Skadi", ["a.png", "b.png"]), ("Amiya", ["c.png"])):
        d = cards / card / "assets"
        d.mkdir(parents=True)
        for fn in files:
            # 内容要各不相同，否则会被 md5 去重掉（真实场景里也是这样）
            (d / fn).write_bytes(b"\x89PNG\r\n\x1a\n" + (card + fn).encode() * 8)
    m = load_plugin(str(Path(td) / "data"))
    m.load_settings()["card_dirs"] = [str(cards)]
    m._SETTINGS_CACHE = {"card_dirs": [str(cards)]}

    print("== 导入人格卡自带的图 ==")
    r = m.import_card_pack("Skadi")
    ck("导入成功", r.get("ok") is True, str(r)[:60])
    idx = m.load_index()
    skadi = [it for it in idx["items"] if it.get("pack") == "Skadi"]
    ck("Skadi 包里有 2 张", len(skadi) == 2, str(len(skadi)))
    ck("导入的直接可用", all(it.get("status") == "ok" for it in skadi), "")
    r2 = m.import_card_pack("Skadi")
    ck("重复导入不重复加", len(m.load_index()["items"]) == 2, str(r2["data"]["text"]))
    m.import_card_pack("Amiya")
    ck("Amiya 单独一个包",
       len([it for it in m.load_index()["items"] if it.get("pack") == "Amiya"]) == 1)

    print()
    print("== 发送优先本卡 ==")
    it = m.pick("", "Skadi")
    ck("Skadi 会话拿到 Skadi 的图", it and it.get("pack") == "Skadi",
       str(it.get("pack") if it else None))
    it2 = m.pick("", "Amiya")
    ck("Amiya 会话拿到 Amiya 的图", it2 and it2.get("pack") == "Amiya",
       str(it2.get("pack") if it2 else None))

    print()
    print("== 收集时打上当前人格的包 ==")
    import base64
    def png_url(tag: bytes) -> str:
        png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 4 + (200).to_bytes(4, "big")
               + (200).to_bytes(4, "big") + tag * 64)
        return "data:image/png;base64," + base64.b64encode(png).decode()

    url = png_url(b"k")
    msg = m.collect_sticker(url, "test", force=True, pack="Kal'tsit")
    ck("收集成功", "收进来了" in msg, msg[:40])
    newest = m.load_index()["items"][-1]
    ck("带着 Kal'tsit 的包", newest.get("pack") == "Kal'tsit", str(newest.get("pack")))

    print()
    print("== 没有本卡的图时退回通用图 ==")
    m.collect_sticker(png_url(b"g"), "x", force=True, pack="")
    generic = [it for it in m.load_index()["items"] if not it.get("pack")]
    ck("通用图存在", len(generic) >= 1, str(len(generic)))
    it3 = m.pick("", "Nobody")
    ck("陌生人格也能挑到图（退通用）", it3 is not None, str(it3.get("pack") if it3 else None))

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
