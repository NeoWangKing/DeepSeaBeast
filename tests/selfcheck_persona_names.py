#!/usr/bin/env python3
"""称呼跟着人格卡走：换成斯卡蒂以后，"大肥鱼"不该再叫得应。"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.channel import ChatRef, InboundMessage, Message, Sender  # noqa: E402
from core.plugin.manager import PluginManager  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


async def main() -> int:
    py = str(ROOT / ".venv/bin/python")
    data = tempfile.mkdtemp()
    # 造两张卡：一张认"斯卡蒂"，一张认"大肥鱼"
    for card, names in (("skadi", ["斯卡蒂", "Skadi"]), ("fish", ["大肥鱼", "小鲸鱼"])):
        d = os.path.join(data, "cards", card)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "prompt.txt"), "w", encoding="utf-8").write("你是%s。" % card)
        open(os.path.join(d, "aliases.txt"), "w", encoding="utf-8").write("\n".join(names) + "\n")
    import json
    with open(os.path.join(data, "mapping__custom__.json"), "w", encoding="utf-8") as f:
        json.dump({"by_group": {"100000001": "skadi", "100000002": "fish"}}, f,
                  ensure_ascii=False)

    mgr = PluginManager()
    await mgr.load({"name": "personas", "command": [py, "plugins/personas/main.py"],
                    "cwd": str(ROOT), "timeout": 10, "priority": 5,
                    "can_persona": True, "data_dir": data}, base_dir=str(ROOT))
    try:
        print("== 插件声明了 names 能力 ==")
        ck("personas 有 names 能力", "names" in mgr.capabilities.get("personas", set()),
           str(mgr.capabilities.get("personas")))

        def scope(gid):
            return {"scope": {"kind": "group", "id": gid, "key": "g%d" % gid},
                    "channel": {"name": "onebot"}}

        print()
        print("== 斯卡蒂卡的称呼 ==")
        r = await mgr.request("personas", "names", scope(100000001))
        ck("卡名对", r.get("card") == "skadi", str(r.get("card")))
        ck("认「斯卡蒂」", "斯卡蒂" in (r.get("aliases") or []), str(r.get("aliases")))
        ck("也认「Skadi」", "Skadi" in (r.get("aliases") or []), str(r.get("aliases")))
        ck("不认「大肥鱼」", "大肥鱼" not in (r.get("aliases") or []), str(r.get("aliases")))

        print()
        print("== 换成小鲸鱼卡就认「大肥鱼」==")
        r2 = await mgr.request("personas", "names", scope(100000002))
        ck("认「大肥鱼」", "大肥鱼" in (r2.get("aliases") or []), str(r2.get("aliases")))

        print()
        print("== 没有 aliases.txt 的卡 = 不认任何文字称呼 ==")
        plain = os.path.join(data, "cards", "plain")
        os.makedirs(plain, exist_ok=True)
        open(os.path.join(plain, "prompt.txt"), "w", encoding="utf-8").write("你是 plain。")
        with open(os.path.join(data, "mapping__custom__.json"), "w", encoding="utf-8") as f:
            json.dump({"by_group": {"100000003": "plain"}}, f, ensure_ascii=False)
        r3 = await mgr.request("personas", "names", scope(100000003))
        ck("返回空列表（退回只看 @）", r3.get("aliases") == [], str(r3))
    finally:
        await mgr.close()

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
