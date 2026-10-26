#!/usr/bin/env python3
"""表情包：**一个公用库** + 人格卡只声明"偏爱哪些标签"（偏好排序，不分区）。"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra[:70]))
    if not cond:
        FAIL.append(name)


def load_plugin(data_dir: str):
    os.environ["DEEPSEA_PLUGIN_DATA"] = data_dir
    spec = importlib.util.spec_from_file_location("stickers", "plugins/stickers/main.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def png(tag: bytes) -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + b"\x00" * 4 + (200).to_bytes(4, "big")
            + (200).to_bytes(4, "big") + tag * 32)


def main() -> int:
    td = Path(tempfile.mkdtemp())
    cards = td / "cards"
    for card, files in (("Skadi", ["a.png", "b.png"]), ("Amiya", ["c.png"])):
        d = cards / card / "assets"
        d.mkdir(parents=True)
        for fn in files:
            (d / fn).write_bytes(png((card + fn).encode()))

    m = load_plugin(str(td / "data"))
    m._SETTINGS_CACHE = {"card_dirs": [str(cards)]}

    print("== 导入人格卡图片：进公用库，只打标签 ==")
    r = m.import_card_pack("Skadi")
    ck("导入成功", r.get("ok") is True, str(r.get("data", {}).get("text"))[:50])
    idx = m.load_index()
    ck("库里 2 张", len(idx["items"]) == 2, str(len(idx["items"])))
    ck("都带 skadi 标签",
       all("skadi" in [t.lower() for t in it.get("tags") or []] for it in idx["items"]), "")
    ck("没有 pack 字段（不按人格分区）",
       all("pack" not in it for it in idx["items"]), "")
    r2 = m.import_card_pack("Skadi")
    ck("重复导入不重复加", len(m.load_index()["items"]) == 2,
       str(r2.get("data", {}).get("text"))[:40])
    m.import_card_pack("Amiya")
    ck("Amiya 的图也进同一个库", len(m.load_index()["items"]) == 3, "")

    print()
    print("== 偏好排序：符合人设的排前面，但别的图照样能用 ==")
    it = m.pick("", ["skadi"])
    ck("有 skadi 偏好时优先挑 skadi 的图", "skadi" in [t.lower() for t in it.get("tags") or []],
       str(it.get("tags")))
    it2 = m.pick("", ["something-random"])
    ck("偏好对不上也能挑到图（不会没图可发）", it2 is not None, str(it2.get("tags")))
    it3 = m.pick("", [])
    ck("没有偏好时正常挑", it3 is not None, str(it3.get("tags")))

    print()
    print("== 收集进公用库 ==")
    msg = m.collect_sticker("data:image/png;base64," + base64.b64encode(png(b"new")).decode(),
                            "test", force=True)
    ck("收集成功", "收进来了" in msg, msg[:40])
    newest = m.load_index()["items"][-1]
    ck("没有 pack 字段", "pack" not in newest, str(list(newest.keys())[:6]))
    ck("默认待打标", newest.get("status") == "pending", str(newest.get("status")))

    print()
    print("== 核心把人格卡 + 表情包偏好传给插件 ==")
    from core.channel import ChatRef, InboundMessage, Message, Sender
    from core.tools import Turn

    class FakePlugins:
        def __init__(self):
            self.turns = []

        def has(self, name):
            return True

        async def call(self, name, args, timeout=None, turn=None):
            self.turns.append({"name": name, "turn": turn})
            return {"ok": True, "data": "ok"}

    class FakeCh:
        name = "onebot"
        abilities = {"text", "sticker"}

        async def send(self, out):
            from core.channel import SendResult
            return SendResult(ok=True)

    fp = FakePlugins()
    msg_in = InboundMessage(chat=ChatRef.group(1), sender=Sender(id=7, name="a"),
                            message=Message.from_text("笑死"), message_id="1")
    turn = Turn(FakeCh(), msg_in, None, plugins=fp,
                plugin_ctx={"persona": {"card": "Skadi", "stickers": ["skadi"]},
                            "scope": {"ref": "sxxx"}})
    asyncio.run(turn.dispatch("send_sticker", {"query": "笑死"}))
    seen = fp.turns[-1]["turn"] if fp.turns else {}
    ck("插件收到人格卡名", (seen.get("persona") or {}).get("card") == "Skadi", str(seen)[:50])
    ck("插件收到表情包偏好", (seen.get("persona") or {}).get("stickers") == ["skadi"],
       str(seen)[:50])
    ck("插件收到会话引用", (seen.get("scope") or {}).get("ref") == "sxxx", str(seen)[:50])

    print()
    print("== 两个插件互不依赖 ==")
    src_personas = (ROOT / "plugins/personas/main.py").read_text(encoding="utf-8")
    src_stickers = (ROOT / "plugins/stickers/main.py").read_text(encoding="utf-8")
    ck("人格插件不 import 表情包插件", "stickers" not in src_personas.split("def ")[0],
       "")
    ck("表情包插件不读人格卡目录",
       "personas" not in src_stickers.replace("personas\", \"cards", ""), "")

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
