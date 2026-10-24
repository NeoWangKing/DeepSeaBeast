#!/usr/bin/env python3
"""发送重试：只重试"明确失败且可重试"的；未确认的绝不重发（怕重复）。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.channel import ChatRef, Message, OutboundMessage, SendResult  # noqa: E402
from core.channel_manager import ChannelManager  # noqa: E402
from core.pacing import PacingConfig  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


class FlakyChannel:
    """按剧本返回一串结果，记录被调用了几次。"""
    name = "flaky"
    supports = {"group"}
    abilities = {"text"}

    def __init__(self, results: list[SendResult]) -> None:
        self.results = list(results)
        self.calls = 0

    async def send(self, out) -> SendResult:
        self.calls += 1
        if self.results:
            return self.results.pop(0)
        return SendResult(ok=True)

    def describe(self, chat):        # noqa: D401
        return "测试"

    def scope_info(self, chat):
        return {"kind": chat.kind, "id": chat.id, "key": chat.key, "label": "测试", "name": ""}

    def profile(self):
        return {"style": "", "inline": {}, "limits": {}}


def mk_msg() -> OutboundMessage:
    return OutboundMessage(chat=ChatRef.group(1), message=Message.from_text("测试"))


async def case(name: str, results: list[SendResult], expect_ok: bool,
               expect_calls: int) -> None:
    ch = FlakyChannel(results)
    mgr = ChannelManager(PacingConfig(), max_retries=2, retry_backoff_sec=0.01)
    mgr.add(ch)
    res = await mgr.send(mk_msg())
    print("  [%s] ok=%s calls=%d error=%r" % (name, res.ok, ch.calls, res.error))
    ck("%s → 结果 %s" % (name, "成功" if expect_ok else "失败"), res.ok is expect_ok)
    ck("%s → 尝试次数 %d" % (name, expect_calls), ch.calls == expect_calls, str(ch.calls))


async def main() -> int:
    print("== 明确失败 → 重试，第二次成功 ==")
    await case("先失败后成功",
               [SendResult(ok=False, error="连接断了", retryable=True), SendResult(ok=True)],
               expect_ok=True, expect_calls=2)

    print()
    print("== 一直失败 → 用完次数就放弃 ==")
    await case("一直失败",
               [SendResult(ok=False, error="超时", retryable=True)] * 3,
               expect_ok=False, expect_calls=3)

    print()
    print("== 未确认（可能已经发出去了）→ 绝不重发 ==")
    await case("未确认",
               [SendResult(ok=True, confirmed=False, error="没有回执")],
               expect_ok=True, expect_calls=1)

    print()
    print("== 硬错误（参数/目标不对）→ 不重试 ==")
    await case("硬错误",
               [SendResult(ok=False, error="群号不对", retryable=False)],
               expect_ok=False, expect_calls=1)

    print()
    print("== 没有可用通道 → 直接失败，不重试 ==")
    mgr = ChannelManager(PacingConfig(), max_retries=2, retry_backoff_sec=0.01)
    res = await mgr.send(mk_msg())
    ck("无通道直接失败", res.ok is False and "没有可用的通道" in res.error, res.error[:30])

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
