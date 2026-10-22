#!/usr/bin/env python3
"""后台任务自测：按时跑、不并发、超时、熔断、插件 job 端到端。"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.plugin import PluginManager  # noqa: E402
from core.scheduler import Scheduler  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


async def main() -> int:
    td = Path(tempfile.mkdtemp())
    try:
        print("== 按时触发 ==")
        s = Scheduler(tick=0.05)
        hits: list[float] = []

        async def job() -> None:
            hits.append(time.time())

        s.add_core_job("tick", 0.3, job, timeout=5)
        await s.start()
        await asyncio.sleep(1.0)
        await s.stop()
        ck("至少跑了 2 次", len(hits) >= 2, "%d 次" % len(hits))

        print()
        print("== 不并发：上一轮没完就跳过 ==")
        s2 = Scheduler(tick=0.05)
        running = 0
        peak = 0

        async def slow() -> None:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.5)
            running -= 1

        s2.add_core_job("slow", 0.1, slow, timeout=5)
        await s2.start()
        await asyncio.sleep(1.2)
        await s2.stop()
        ck("同一 job 从不并发", peak == 1, "峰值并发 %d" % peak)

        print()
        print("== 超时被中断 ==")
        s3 = Scheduler(tick=0.05)

        async def hang() -> None:
            await asyncio.sleep(10)

        j = s3.add_core_job("hang", 0.2, hang, timeout=0.3)
        await s3.start()
        await asyncio.sleep(1.0)
        await s3.stop()
        ck("记了失败", j.fails >= 1, "fails=%d" % j.fails)
        ck("错误是 timeout", j.last_error == "timeout", j.last_error)

        print()
        print("== 熔断：连续失败 3 次就停用 ==")
        s4 = Scheduler(tick=0.05)

        async def boom() -> None:
            raise RuntimeError("炸了")

        j2 = s4.add_core_job("boom", 0.1, boom, timeout=5)
        await s4.start()
        await asyncio.sleep(1.2)
        await s4.stop()
        ck("已熔断", j2.disabled is True, "fails=%d" % j2.fails)
        ck("失败次数到 3", j2.fails >= 3, str(j2.fails))

        print()
        print("== 插件声明的 job 端到端 ==")
        py = str(ROOT / ".venv/bin/python")
        mgr = PluginManager()
        await mgr.load({"name": "echo_plugin",
                        "command": [py, "plugins/echo_plugin/main.py"],
                        "cwd": str(ROOT), "timeout": 10,
                        "data_dir": str(td / "echo")}, base_dir=str(ROOT))
        specs = [sp for _n, sp, _p in mgr.iter_jobs()]
        ck("插件声明了 job", any(x["name"] == "heartbeat" for x in specs), str(specs))
        r = await mgr.run_job("echo_plugin", "heartbeat")
        ck("run_job 返回 ok", isinstance(r, dict) and r.get("ok") is True, str(r))
        beat = td / "echo" / "heartbeat.log"
        ck("插件在自己的数据目录写了东西", beat.exists() and beat.read_text().strip() != "")
        await mgr.close()

        print()
        if FAIL:
            print("FAILED: %s" % "、".join(FAIL))
            return 1
        print("全部通过")
        return 0
    finally:
        shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
