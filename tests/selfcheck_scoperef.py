#!/usr/bin/env python3
"""会话引用自测：核心存储只认不透明引用，人话身份只在登记表和提示词里。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.scoperef import ScopeRegistry, scope_ref  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


def main() -> int:
    print("== 引用是确定的 ==")
    a = scope_ref("onebot", "g1109525246")
    b = scope_ref("onebot", "g1109525246")
    ck("同通道同 key → 同引用", a == b, a)
    ck("引用里没有原始 id", "1109525246" not in a, a)

    print()
    print("== 不同通道不会串 ==")
    d = scope_ref("tcp", "cli")
    e = scope_ref("socket", "cli")
    f = scope_ref("cli", "cli")
    ck("tcp / socket / cli 三个引用互不相同", len({d, e, f}) == 3, "%s %s %s" % (d, e, f))
    ck("同一个原始 key 'cli' 不再是同一个会话", d != e)

    print()
    print("== 登记表只给人看 ==")
    td = Path(tempfile.mkdtemp())
    reg = ScopeRegistry(td)
    reg.remember(a, "onebot", "g1109525246", kind="group", label="群聊", name="测试群")
    desc = reg.describe(a)
    ck("能反查到人话身份", "群聊" in desc and "测试群" in desc, desc)
    ck("登记表落盘", (td / "scopes.json").exists())
    reg2 = ScopeRegistry(td)
    ck("重新加载读得到", "测试群" in reg2.describe(a))

    print()
    print("== 核心数据文件里不带平台 id ==")
    from core.channel import ChatRef, InboundMessage, Message, Sender, SendResult
    from core.pipeline import GateConfig
    from core.runtime import Runtime
    import asyncio

    class Ch:
        name = "onebot"
        supports = {"group"}
        abilities = {"text"}
        async def send(self, o): return SendResult(ok=True)
        def scope_info(self, chat):
            return {"kind": chat.kind, "id": chat.id, "key": chat.key,
                    "label": "群聊", "name": "测试群"}
        def name_for(self, chat): return "onebot"

    async def run() -> str:
        d = tempfile.mkdtemp()
        rt = Runtime(Ch(), GateConfig.from_dict({"force_mode": "peak", "reply_probability": 1.0}),
                     [1], reply_fn=lambda m, c=None: _ans(), data_dir=d)
        m = InboundMessage(chat=ChatRef.group(1109525246), sender=Sender(1, "灵其啊"),
                           message=Message.from_text("在吗。"), message_id="1", addressed=True)
        await rt.on_message(m)
        return d

    async def _ans() -> str:
        return "在"

    d = asyncio.run(run())
    names = []
    for root, _dirs, files in os.walk(d):
        for f in files:
            names.append(os.path.join(root, f))
    leaked = [p for p in names if "1109525246" in p]
    ck("文件名里没有群号", not leaked, str(leaked))
    ck("recent 用引用命名", any("/recent/s" in p for p in names), str([os.path.relpath(p, d) for p in names]))

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
