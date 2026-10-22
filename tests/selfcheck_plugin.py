#!/usr/bin/env python3
"""插件协议 v1 自测：握手/协议校验/工具/事件/健康检查/崩溃重启/熔断/优雅退出。"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.plugin import PluginError, PluginManager, PluginTimeout  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-48s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


def bad_plugin(dirpath: Path, protocol: int) -> None:
    """一个协议版本不兼容的插件。"""
    (dirpath / "bad.py").write_text(
        "import json,sys\n"
        "for line in sys.stdin:\n"
        "    m=json.loads(line)\n"
        "    if m.get('method')=='hello':\n"
        "        print(json.dumps({'id':m['id'],'result':{'name':'bad','version':'1.0',"
        "'protocol':%d,'tools':[],'events':[]}}),flush=True)\n" % protocol,
        encoding="utf-8")


async def main() -> int:
    td = Path(tempfile.mkdtemp())
    py = str(ROOT / ".venv/bin/python")
    script = str(ROOT / "plugins/echo_plugin/main.py")
    mgr = PluginManager()
    try:
        print("== 握手（协议 v1）==")
        await mgr.load({"name": "echo_plugin", "command": [py, script], "cwd": str(ROOT),
                        "timeout": 10, "data_dir": str(td / "echo_data")},
                       base_dir=str(ROOT))
        ck("工具已注册", {"echo_say", "echo_slow"} <= set(mgr.tools), str(sorted(mgr.tools)))
        ck("订阅了事件", mgr.events.get("echo_plugin") == {"message.received", "reply.sent"},
           str(mgr.events.get("echo_plugin")))
        ck("数据目录已创建", (td / "echo_data").is_dir())

        print()
        print("== 健康检查 ==")
        h = await mgr.health()
        ck("ping 通", h["echo_plugin"]["pong"] is True, str(h["echo_plugin"]))

        print()
        print("== 调工具（v1 结构化返回）==")
        r = await mgr.call("echo_say", {"text": "你好"})
        ck("返回 ok+data", r.get("ok") is True and r.get("data", {}).get("text") == "你好", str(r))

        print()
        print("== 事件投递 ==")
        await mgr.emit("message.received", {"scope": {"kind": "group", "id": 1},
                                            "text": "测试事件", "ts": 1.0})
        await asyncio.sleep(0.3)
        evf = td / "echo_data" / "events.jsonl"
        got = evf.read_text(encoding="utf-8") if evf.exists() else ""
        ck("事件到达插件", "message.received" in got and "测试事件" in got, got[:60])

        print()
        print("== 超时 ==")
        try:
            await mgr.call("echo_slow", {"seconds": 3}, timeout=0.5)
            ck("超时抛异常", False)
        except PluginTimeout as e:
            ck("超时抛 PluginTimeout", True, str(e)[:34])

        print()
        print("== 崩溃自动重启 ==")
        proc = mgr._procs["echo_plugin"]
        proc._proc.kill()
        await asyncio.sleep(0.3)
        r = await mgr.call("echo_say", {"text": "还活着吗"})
        ck("重启后可用", r.get("data", {}).get("text") == "还活着吗", str(r))
        ck("记录了重启", proc._restarts >= 1, str(proc._restarts))

        print()
        print("== 未知工具 ==")
        try:
            await mgr.call("不存在", {})
            ck("未知工具应报错", False)
        except PluginError as e:
            ck("未知工具报 PluginError", True, str(e))

        print()
        print("== 协议版本不兼容 ==")
        bad_plugin(td, protocol=2)
        mgr2 = PluginManager()
        try:
            await mgr2.load({"name": "bad", "command": [py, str(td / "bad.py")],
                             "cwd": str(ROOT), "timeout": 5}, base_dir=str(ROOT))
            ck("版本不符应拒绝加载", False)
        except PluginError as e:
            ck("版本不符被拒绝", "协议版本" in str(e), str(e)[:40])
        finally:
            await mgr2.close()

        print()
        print("== Turn 集成 ==")
        from core.channel import ChatRef, InboundMessage, Message, SendResult, Sender
        from core.tools import Turn

        class _Ch:
            name = "cli"
            supports = {"local"}
            abilities = {"text"}

            async def send(self, out):
                return SendResult(ok=True)

        t = Turn(_Ch(), InboundMessage(chat=ChatRef.local(), sender=Sender(1, "t"),
                                       message=Message.from_text("嗨")), None, plugins=mgr)
        names = [s["function"]["name"] for s in t.schemas]
        ck("插件工具进 schema", "echo_say" in names, str(names))
        out = await t.dispatch("echo_say", {"text": "来自插件"})
        ck("dispatch 走插件并解包", "来自插件" in out, out[:40])

        print()
        print("== 优雅退出 ==")
        proc_ref = mgr._procs["echo_plugin"]
        await mgr.close()
        p2 = proc_ref._proc
        ck("进程已退出", p2 is None or p2.returncode is not None,
           "returncode=%s" % (getattr(p2, "returncode", "?"),))
    finally:
        await mgr.close()
        shutil.rmtree(td, ignore_errors=True)

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
