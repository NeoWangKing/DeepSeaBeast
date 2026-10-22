#!/usr/bin/env python3
"""能力与诚实失败自测：能力矩阵、意图执行、不支持就报错。"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.capability import build_report, supports  # noqa: E402
from core.channel import ChatRef, InboundMessage, Message, OutboundMessage, SendResult, Sender  # noqa: E402
from core.tools import ToolSpec, Turn  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


class Ch:
    def __init__(self, name: str, abilities: set[str]) -> None:
        self.name = name
        self.abilities = abilities
        self.supports = {"group"}
        self.sent: list[tuple[str, dict]] = []

    async def send(self, out: OutboundMessage) -> SendResult:
        for seg in out.message.segments:
            if seg.kind in ("file", "image") and seg.kind not in self.abilities:
                return SendResult(ok=False, error="这个通道发不了 %s" % seg.kind)
        for seg in out.message.segments:
            self.sent.append((seg.kind, dict(seg.data)))
        return SendResult(ok=True)


class Plugins:
    """假的插件管理器：只实现 has / call。"""

    def __init__(self, result) -> None:
        self.result = result

    def has(self, name: str) -> bool:
        return name == "fake_tool"

    async def call(self, name, args, timeout=None, turn=None):
        return self.result


def mk_turn(ch, result, specs):
    msg = InboundMessage(chat=ChatRef.group(1), sender=Sender(9, "谁"),
                         message=Message.from_text("发个文件"), message_id="1", at_me=True)
    t = Turn(ch, msg, None, abilities=ch.abilities, specs=specs, plugins=Plugins(result))
    t.decision = {"action": "reply", "intent": ""}
    return t


def main() -> int:
    print("== 能力矩阵 ==")
    class C:
        def __init__(self, n, a):
            self.name, self.abilities = n, a

    tools = [ToolSpec("send_file", "", {}, requires={"file"}),
             ToolSpec("say", "", {}, requires={"text"})]
    rep = build_report([C("onebot", {"text", "file"}), C("tcp", {"text"})], tools)
    ck("矩阵列出通道能力", rep.channels["onebot"] == ["file", "text"], str(rep.channels))
    ck("缺口被指出来", any("tcp" in x and "file" in x for x in rep.unavailable), str(rep.unavailable))
    ck("text 不缺", not any("say" in x for x in rep.unavailable))
    ck("supports 判断正确", supports({"text"}, "text") and not supports({"text"}, "file"))

    print()
    print("== 插件给意图，核心执行 ==")
    specs = []   # 插件工具不经过内置 specs

    async def case_file_supported():
        ch = Ch("onebot", {"text", "file"})
        t = mk_turn(ch, {"ok": True, "data": {"send": [
            {"kind": "text", "text": "日志在这"},
            {"kind": "file", "path": "/tmp/x.log", "name": "x.log"},
        ]}}, specs)
        r = await t.dispatch("fake_tool", {})
        ck("文本+文件都发出去了", [k for k, _ in ch.sent] == ["text", "file"], str(ch.sent))
        ck("返回说明", "已执行" in r, r[:40])

    asyncio.run(case_file_supported())

    print()
    print("== 通道不支持 → 诚实报错，不静默降级 ==")

    async def case_file_unsupported():
        ch = Ch("tcp", {"text"})
        t = mk_turn(ch, {"ok": True, "data": {"send": [
            {"kind": "text", "text": "先说话"},
            {"kind": "file", "path": "/tmp/x.log"},
        ]}}, specs)
        r = await t.dispatch("fake_tool", {})
        ck("报错里说明能力缺失", "做不到" in r and "file" in r, r[:50])
        ck("文件没有被偷偷换成文本", all(k != "file" for k, _ in ch.sent), str(ch.sent))

    asyncio.run(case_file_unsupported())

    print()
    print("== 插件报错照原样传回模型 ==")

    async def case_plugin_error():
        ch = Ch("onebot", {"text", "file"})
        t = mk_turn(ch, {"ok": False, "error": "文件不存在"}, specs)
        r = await t.dispatch("fake_tool", {})
        ck("插件错误传给模型", "文件不存在" in r, r[:40])

    asyncio.run(case_plugin_error())

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
