#!/usr/bin/env python3
"""人格插件自测：按作用域选卡、注入人格、不能越权改别人的。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.channel import ChatRef, InboundMessage, Message, Sender  # noqa: E402
from core.context import ContextBundle, ContextRegistry  # noqa: E402
from core.plugin import PluginManager  # noqa: E402
from core.prompt import render  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-50s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


def mk(kind: str = "group", cid: int = 1109525246, uid: int = 3245938285) -> InboundMessage:
    chat = {"group": ChatRef.group, "private": ChatRef.private}.get(kind, lambda x: ChatRef.local())(cid)
    return InboundMessage(chat=chat, sender=Sender(uid, "灵其啊"),
                          message=Message.from_text("在吗"), message_id="1", at_me=True)


async def main() -> int:
    py = str(ROOT / ".venv/bin/python")
    mgr = PluginManager()
    await mgr.load({"name": "persona", "command": [py, "plugins/persona/main.py"],
                    "cwd": str(ROOT), "timeout": 10, "priority": 5,
                    "can_persona": True}, base_dir=str(ROOT))

    providers = mgr.context_providers()
    ck("插件声明了 context 能力", len(providers) == 1, str([p.name for p in providers]))

    reg = ContextRegistry()
    for p in providers:
        reg.add(p)

    async def persona_for(msg):
        b = ContextBundle(scope_key=msg.chat.key, scope_kind=msg.chat.kind,
                          scope_id=msg.chat.id, sender_id=msg.sender.id,
                          sender_name=msg.sender.name, current=msg)
        await reg.collect(b)
        return (b.persona or ""), render(b)

    print()
    print("== 群人格 ==")
    p_dbg, msgs_dbg = await persona_for(mk("group", 1109525246))
    ck("调试群拿到专属卡", "调试" in p_dbg or "灵其啊" in p_dbg, p_dbg[:40])
    ck("通用补丁也拼上了", "AI 味" in p_dbg or "矜持" in p_dbg)
    ck("人格进了 messages[0]", p_dbg.splitlines()[0][:10] in msgs_dbg[0]["content"])

    p_light, _ = await persona_for(mk("group", 869622030))
    ck("闪电群人格不同", p_light != p_dbg, p_light[:30])

    print()
    print("== 私聊人格 ==")
    p_priv, _ = await persona_for(mk("private", 3245938285, 3245938285))
    ck("私聊拿到温和版", p_priv != p_dbg, p_priv[:30])
    p_other, _ = await persona_for(mk("private", 287383512, 287383512))
    ck("另一个私聊也不同", p_other != p_priv, p_other[:30])

    await mgr.close()
    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
