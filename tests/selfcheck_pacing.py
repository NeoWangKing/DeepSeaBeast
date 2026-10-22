#!/usr/bin/env python3
"""节奏自测：多条之间的间隔、首条不等待、开关、封顶、抖动范围。"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.pacing import Pacer, PacingConfig  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


async def main() -> int:
    print("== 首条不等待 ==")
    p = Pacer(PacingConfig(base_sec=0.05, sec_per_char=0.01, max_sec=0.2, jitter=0.0))
    d = await p.before_send("g1", "第一句")
    ck("第一条延迟为 0", d == 0.0, str(d))

    print()
    print("== 间隔按上一条字数算 ==")
    p.reset("g1")
    await p.before_send("g1", "短")            # 1 字 → 下一条 0.05 + 0.01 = 0.06
    d1 = p.delay_for("g1")
    await p.before_send("g1", "长" * 10)       # 10 字 → 0.05 + 0.10 = 0.15
    d2 = p.delay_for("g1")
    ck("短句后约 0.06s", abs(d1 - 0.06) < 0.01, "%.3f" % d1)
    ck("长句后约 0.15s", abs(d2 - 0.15) < 0.01, "%.3f" % d2)

    print()
    print("== 封顶 ==")
    p2 = Pacer(PacingConfig(base_sec=1.0, sec_per_char=1.0, max_sec=2.5, jitter=0.0))
    await p2.before_send("g", "x" * 100)
    ck("超长句被压到 max_sec", p2.delay_for("g") == 2.5, str(p2.delay_for("g")))

    print()
    print("== 关掉就没延迟 ==")
    p3 = Pacer(PacingConfig(enabled=False))
    await p3.before_send("g", "x" * 100)
    ck("enabled=False → 0", p3.delay_for("g") == 0.0)

    print()
    print("== 抖动在范围内 ==")
    p4 = Pacer(PacingConfig(base_sec=1.0, sec_per_char=0.0, max_sec=10, jitter=0.2))
    await p4.before_send("g", "a")
    ds = [p4.delay_for("g") for _ in range(50)]
    ck("全部落在 ±20%%（%.2f~%.2f）" % (min(ds), max(ds)),
       all(0.8 <= x <= 1.2 for x in ds))
    ck("不是常数（有随机）", len(set(round(x, 4) for x in ds)) > 5)

    print()
    print("== 不同会话互不影响 ==")
    p5 = Pacer(PacingConfig(base_sec=0.5, sec_per_char=0.0, max_sec=9, jitter=0.0))
    await p5.before_send("gA", "a")
    ck("另一个会话首条不等待", p5.delay_for("gB") == 0.0)
    ck("本会话仍要等", p5.delay_for("gA") == 0.5)

    print()
    print("== 端到端：3 条气泡的真实间隔 ==")
    from core.channel import ChatRef, Message, OutboundMessage, SendResult
    from core.channel_manager import ChannelManager

    stamps: list[float] = []

    class Ch:
        name = "cli"
        supports = {"local"}
        abilities = {"text"}

        async def send(self, out):
            stamps.append(time.time())
            return SendResult(ok=True)

        async def start(self, on):
            pass

        async def stop(self):
            pass

    mgr = ChannelManager(PacingConfig(base_sec=0.08, sec_per_char=0.01, max_sec=0.3, jitter=0.0))
    mgr.add(Ch())
    chat = ChatRef.local()
    mgr.reset_pacing(chat)
    t0 = time.time()
    for t in ("一", "二二", "三三三"):
        await mgr.send(OutboundMessage(chat=chat, message=Message.from_text(t)))
    gaps = [stamps[0] - t0, stamps[1] - stamps[0], stamps[2] - stamps[1]]
    ck("第 1 条几乎不延迟", gaps[0] < 0.03, "%.3f" % gaps[0])
    ck("第 2 条间隔约 0.09s", 0.06 < gaps[1] < 0.15, "%.3f" % gaps[1])
    ck("第 3 条间隔约 0.10s", 0.07 < gaps[2] < 0.16, "%.3f" % gaps[2])

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
