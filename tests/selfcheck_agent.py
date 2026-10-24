#!/usr/bin/env python3
"""工具循环单测：用脚本化的假模型，验证 decide/send/finish 的约束。

用法：.venv/bin/python tests/selfcheck_agent.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.agent import run_turn  # noqa: E402
from core.channel import ChatRef, InboundMessage, Message, OutboundMessage, Sender, SendResult  # noqa: E402
from core.llm import LLMResponse, ToolCall  # noqa: E402
from core.tools import Turn  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-48s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


class FakeChannel:
    name = "cli"
    abilities = {"text"}

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, out: OutboundMessage) -> SendResult:
        self.sent.append(out.message.text)
        return SendResult(ok=True, message_id="m1")


class FakeLLM:
    pass


def script(*rounds: list[ToolCall]) -> callable:
    """按顺序返回预设的工具调用。"""
    it = iter(rounds)

    def _chat_tools(messages, tools, llm, choice, max_tokens):
        try:
            calls = next(it)
        except StopIteration:
            calls = []
        return LLMResponse(content="", tool_calls=calls, finish_reason="tool_calls")
    return _chat_tools


def mk_msg(text: str = "在吗") -> InboundMessage:
    return InboundMessage(chat=ChatRef.local(), sender=Sender(1, "测试"),
                          message=Message.from_text(text), message_id="m0", addressed=True)


async def case(name: str, rounds: list[list[ToolCall]]) -> Turn:
    import core.agent as A
    ch = FakeChannel()
    turn = Turn(ch, mk_msg(), FakeLLM())
    real = A.chat_tools
    A.chat_tools = script(*rounds)
    try:
        await run_turn(turn, [{"role": "user", "content": "在吗"}])
    finally:
        A.chat_tools = real
    ck(name, True, "sent=%r decision=%r finished=%s" % (ch.sent, turn.decision, turn.finished))
    return turn


async def main() -> int:
    print("== 正常：decide(reply) → send → finish ==")
    t = await case("走完三轮", [
        [ToolCall("1", "decide", {"action": "reply", "intent": "打招呼"})],
        [ToolCall("2", "send_message", {"text": "在的"})],
        [ToolCall("3", "finish", {"reason": "说完了"})],
    ])
    ck("真的说了一句", t.sent == ["在的"], str(t.sent))
    ck("收工了", t.finished)

    print()
    print("== silence：一句都不发 ==")
    t = await case("decide(silence)", [
        [ToolCall("1", "decide", {"action": "silence"})],
        [ToolCall("2", "send_message", {"text": "不该出现"})],
    ])
    ck("没说话", t.sent == [], str(t.sent))

    print()
    print("== 没 decide 就说话：被拒 ==")
    t = await case("先说话", [
        [ToolCall("1", "send_message", {"text": "抢话"})],
        [ToolCall("2", "decide", {"action": "reply"})],
        [ToolCall("3", "send_message", {"text": "这才对"})],
        [ToolCall("4", "finish", {})],
    ])
    ck("第一条被拒，只发出后一条", t.sent == ["这才对"], str(t.sent))

    print()
    print("== 连发上限 ==")
    t = await case("连发 6 条", [
        [ToolCall("1", "decide", {"action": "reply"})],
        [ToolCall("2", "send_message", {"text": "1"})],
        [ToolCall("3", "send_message", {"text": "2"})],
        [ToolCall("4", "send_message", {"text": "3"})],
        [ToolCall("5", "send_message", {"text": "4"})],
        [ToolCall("6", "send_message", {"text": "5"})],
    ])
    ck("最多 4 条", len(t.sent) == 4, str(t.sent))

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
