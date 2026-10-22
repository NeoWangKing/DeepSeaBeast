#!/usr/bin/env python3
"""插件协议自测：握手、调工具、崩溃重启、超时。

用法：.venv/bin/python tests/selfcheck_plugin.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.plugin import PluginError, PluginManager, PluginProcess, PluginTimeout  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


CUDA = None


async def main() -> int:
    py = str(ROOT / ".venv/bin/python")
    script = str(ROOT / "plugins/echo_plugin/main.py")

    print("== 握手 + 工具清单 ==")
    mgr = PluginManager()
    await mgr.load({"name": "echo_plugin", "command": [py, script],
                    "cwd": str(ROOT), "timeout": 10}, base_dir=str(ROOT))
    ck("工具已注册", {"echo_say", "echo_slow"} <= set(mgr.tools), str(sorted(mgr.tools)))
    ck("schema 能生成", len(mgr.schemas({"text"})) == 2)
    ck("能力受限时也生成（无 requires）", len(mgr.schemas(set())) == 2)

    print()
    print("== 调工具 ==")
    r = await mgr.call("echo_say", {"text": "你好"})
    ck("echo_say 返回正确", r == {"text": "你好"}, str(r))

    print()
    print("== 超时 ==")
    try:
        await mgr.call("echo_slow", {"seconds": 3}, timeout=0.5)
        ck("超时应该抛异常", False)
    except PluginTimeout as e:
        ck("超时抛 PluginTimeout", True, str(e)[:40])

    print()
    print("== 崩溃自动重启 ==")
    proc = mgr._procs["echo_plugin"]
    proc._proc.kill()                       # 模拟插件被杀
    await asyncio.sleep(0.3)
    try:
        r = await mgr.call("echo_say", {"text": "还活着吗"})
        ck("插件被重启后仍可用", r == {"text": "还活着吗"}, str(r))
    except Exception as e:
        ck("插件被重启后仍可用", False, repr(e))
    ck("记录了重启次数", mgr._procs["echo_plugin"]._restarts >= 1,
       str(mgr._procs["echo_plugin"]._restarts))

    print()
    print("== 未知工具 ==")
    try:
        await mgr.call("不存在", {})
        ck("未知工具应报错", False)
    except PluginError as e:
        ck("未知工具报 PluginError", True, str(e))

    print()
    print("== Turn 里也能调插件工具 ==")
    from core.channel import ChatRef, InboundMessage, Message, OutboundMessage, Sender, SendResult
    from core.tools import Turn

    class _Ch:
        name = "cli"
        abilities = {"text"}

        async def send(self, out):
            return SendResult(ok=True)

    t = Turn(_Ch(), InboundMessage(chat=ChatRef.local(), sender=Sender(1, "t"),
                                   message=Message.from_text("嗨")), None,
             plugins=mgr)
    ok_schema = any(s["function"]["name"] == "echo_say" for s in t.schemas)
    ck("插件工具进了 schema", ok_schema, str([s["function"]["name"] for s in t.schemas]))
    out = await t.dispatch("echo_say", {"text": "来自插件"})
    ck("dispatch 走插件", "来自插件" in out, out[:40])

    await mgr.close()
    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
