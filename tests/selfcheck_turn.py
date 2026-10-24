#!/usr/bin/env python3
"""回合模型自测：打断、预算、超时、追踪、合并后整体文本。"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.channel import ChatRef, InboundMessage, Message, SendResult, Sender  # noqa: E402
from core.pipeline import GateConfig  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.turn import TurnConfig, TurnContext  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


class Ch:
    name = "cli"
    supports = {"local"}
    abilities = {"text"}

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, out):
        self.sent.append(out.message.text)
        return SendResult(ok=True)

    async def start(self, on):
        pass

    async def stop(self):
        pass

    def describe(self, chat):
        return "测试终端"

    def reset_pacing(self, chat):
        pass


def mk(text: str, uid: int = 1) -> InboundMessage:
    return InboundMessage(chat=ChatRef.local(), sender=Sender(uid, "灵其啊"),
                          message=Message.from_text(text), message_id="1", addressed=True)


CFG = GateConfig.from_dict({"force_mode": "peak", "reply_probability": 1.0,
                            "min_interval_sec": 0, "burst_suppress_sec": 0,
                            "typing_wait_sec": 0.2, "merge_window_sec": 0.2,
                            "max_auto_per_hour": 99})


async def case_interrupt() -> None:
    print("== 打断：她正在说，新消息来了 ==")
    ch = Ch()
    started = asyncio.Event()
    ctx_seen: list[TurnContext] = []

    async def slow_reply(msg, ctx):
        ctx_seen.append(ctx)
        started.set()
        await asyncio.sleep(1.0)          # 模拟正在生成
        return "第一轮的回答"

    rt = Runtime(ch, CFG, [1], reply_fn=slow_reply, data_dir=tempfile.mkdtemp())
    t1 = asyncio.create_task(rt.on_message(mk("第一句")))
    await started.wait()
    t2 = asyncio.create_task(rt.on_message(mk("等等，我说错了")))
    await asyncio.gather(t1, t2)
    ck("第一个回合被取消", ctx_seen[0].cancelled is True, ctx_seen[0].cancel_reason)
    ck("取消原因是新消息", "打断" in ctx_seen[0].cancel_reason, ctx_seen[0].cancel_reason)
    ck("第二个回合正常", ctx_seen[1].cancelled is False)
    ck("两个回合 id 不同", ctx_seen[0].id != ctx_seen[1].id)


async def case_budget() -> None:
    print()
    print("== 预算：token 超了就不许再发 ==")
    ctx = TurnContext(id="t-test", scope_key="cli", cfg=TurnConfig(max_tokens=100))
    ctx.add_usage({"total_tokens": 150})
    ck("超预算被识别", ctx.over_budget is True, str(ctx.tokens_used))
    ck("should_stop 给出原因", ctx.should_stop().startswith("budget"), ctx.should_stop())

    ch = Ch()
    from core.llm import LLMResponse, ToolCall
    from core.tools import Turn

    turn = Turn(ch, mk("在吗"), None, ctx=ctx)
    turn.decision = {"action": "reply", "intent": ""}
    out = await turn._do_send({"text": "不该发出去"})
    ck("超预算后 send_message 被拒", "不要再发" in out and not ch.sent, out)


async def case_timeout() -> None:
    print()
    print("== 超时 ==")
    ctx = TurnContext(id="t-slow", scope_key="cli",
                      cfg=TurnConfig(max_seconds=0.1, max_tokens=99999))
    ctx.started_at = time.time() - 1.0
    ck("超时被识别", ctx.expired is True)
    ck("should_stop 给出 timeout", ctx.should_stop().startswith("timeout"), ctx.should_stop())


async def case_sends_limit() -> None:
    print()
    print("== 条数上限来自 TurnConfig ==")
    ctx = TurnContext(id="t-sends", scope_key="cli", cfg=TurnConfig(max_sends=2))
    ch = Ch()
    from core.tools import Turn

    turn = Turn(ch, mk("在吗"), None, ctx=ctx)
    turn.decision = {"action": "reply", "intent": ""}
    for i in range(4):
        await turn._do_send({"text": "第 %d 条" % (i + 1)})
    ck("最多 2 条", ch.sent == ["第 1 条", "第 2 条"], str(ch.sent))
    ck("ctx 记了条数", ctx.sends == 2, str(ctx.sends))


async def case_merge_wait() -> None:
    print()
    print("== 话没说完 → 等；说完了 → 立刻回 ==")
    from core.pipeline import Pipeline

    p = Pipeline(GateConfig.from_dict({"force_mode": "off", "allowed_groups": [1],
                                       "typing_wait_sec": 5.0, "merge_window_sec": 5.0}))

    def g(text):
        m = InboundMessage(chat=ChatRef.group(1), sender=Sender(9, "谁"),
                           message=Message.from_text(text), message_id="1", addressed=True)
        return m

    t0 = time.time()
    r1 = p.run(g("我跟你"), t0)                 # 没说完 → defer
    ck("「我跟你」被延后", r1.kind == "defer", r1.reason)
    r2 = p.run(g("说个事啊"), t0 + 1)            # 仍没说完？其实是完整的 → 立刻回合并文本
    ck("补完整后立刻放行", r2.kind == "reply", r2.kind)
    ck("回的是合并后的整句", r2.text == "我跟你 说个事啊", r2.text)
    r3 = p.run(g("在吗"), t0 + 100)              # 间隔很久 → 新的一串，且完整
    ck("完整短句立刻回", r3.kind == "reply", r3.kind)


async def case_wait_recheck() -> None:
    print()
    print("== 她说\"先等等\" → 过几秒再决定一次（只一次）==")
    from core.llm import LLMResponse, ToolCall
    import core.agent as A
    from core.tools import Turn

    class _LLM:
        pass

    calls: list[str] = []
    script = [
        [ToolCall("1", "decide", {"action": "wait"})],                 # 第一次：先等等
        [ToolCall("2", "decide", {"action": "reply", "intent": "接"})],
        [ToolCall("3", "send_message", {"text": "在的"})],
        [ToolCall("4", "finish", {})],
    ]
    it = iter(script)

    def fake_tools(messages, tools, llm, choice, max_tokens):
        try:
            return LLMResponse(tool_calls=next(it), usage={"total_tokens": 10})
        except StopIteration:
            return LLMResponse(tool_calls=[])

    ch = Ch()
    real = A.chat_tools
    A.chat_tools = fake_tools
    try:
        cfg2 = GateConfig.from_dict({"force_mode": "peak", "reply_probability": 1.0,
                                     "min_interval_sec": 0, "burst_suppress_sec": 0})
        rt = Runtime(ch, cfg2, [1], data_dir=tempfile.mkdtemp(),
                     turn_cfg=TurnConfig(wait_recheck_sec=0.2))
        rt.llm = _LLM()
        await rt.on_message(mk("在吗"))
        await asyncio.sleep(1.2)               # 等重判跑完
    finally:
        A.chat_tools = real
    ck("重判后说了话", ch.sent == ["在的"], str(ch.sent))


async def case_timer_reset() -> None:
    print()
    print("== 每说一句都重新计时（不会 8 秒一到就抢话）==")
    from core.pipeline import Pipeline

    cfg = GateConfig.from_dict({"force_mode": "off", "allowed_groups": [1],
                                "typing_wait_sec": 1.0, "typing_max_wait_sec": 30.0})
    p = Pipeline(cfg)

    def g(text):
        return InboundMessage(chat=ChatRef.group(1), sender=Sender(9, "谁"),
                              message=Message.from_text(text), message_id="1", addressed=True)

    t = 0.0
    r1 = p.run(g("我跟你"), t)          # 第 0 秒，没说完 → 等
    r2 = p.run(g("说个事"), t + 0.9)     # 第 0.9 秒补一句，仍然没说完 → 继续等
    r3 = p.run(g("关于那个"), t + 1.8)   # 第 1.8 秒又补 → 还在等（总时长已超 1 秒窗口）
    r4 = p.run(g("就是那个事啊"), t + 2.7)  # 补完整了 → 立刻回整句
    ck("第 1 句：等", r1.kind == "defer", r1.reason)
    ck("第 2 句：重新计时继续等", r2.kind == "defer", r2.reason)
    ck("第 3 句：总时长超窗口仍在等", r3.kind == "defer", r3.reason)
    ck("补完整立刻回", r4.kind == "reply", r4.kind)
    ck("回的是整串", r4.text == "我跟你 说个事 关于那个 就是那个事啊", r4.text)

    # 硬上限：一直不说完，超过 typing_max_wait_sec 就不再等
    p2 = Pipeline(GateConfig.from_dict({"force_mode": "off", "allowed_groups": [1],
                                        "typing_wait_sec": 1.0,
                                        "typing_max_wait_sec": 3.0}))
    p2.run(g("我想"), 0.0)              # 第 0 秒
    p2.run(g("再想想"), 0.9)            # 间隔 < 窗口 → 继续等（累计 0.9s）
    p2.run(g("那个"), 1.8)              # 累计 1.8s
    r = p2.run(g("关于"), 2.7)          # 累计 2.7s
    r2 = p2.run(g("那个事"), 3.7)       # 累计 3.7s > 上限 3s → 不再等
    ck("窗口内累计未超上限时仍然等", r.kind == "defer", r.reason)
    ck("超过硬上限就不再等", r2.kind == "reply", r2.reason + " " + r2.kind)


async def main() -> int:
    await case_interrupt()
    await case_budget()
    await case_timeout()
    await case_sends_limit()
    await case_merge_wait()
    await case_wait_recheck()
    await case_timer_reset()
    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
