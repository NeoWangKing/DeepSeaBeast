"""后台任务：她在没人说话的时候自己做的事。

机制在核心（定时、单实例、超时、熔断、日志），**具体任务由插件声明**：

    hello 里声明 jobs = [{name, interval_sec, jitter, timeout, description}]
    核心按间隔调 {"method": "run_job", "params": {"job": "refresh_impression"}}

三条硬约定：
  · **不并发**：同一个 job 上一轮没跑完就直接跳过（否则会堆积）
  · **有超时**：超时就杀这一轮（协程取消），记一笔失败
  · **会熔断**：连续失败 3 次就停掉这个 job 并报 ERROR，不影响消息处理
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger("scheduler")

MAX_FAILS = 3


@dataclass(slots=True)
class Job:
    """一个定时任务。"""

    id: str                       # 唯一标识：<plugin>:<name> 或 core:<name>
    name: str
    interval: float               # 秒
    timeout: float = 60.0
    jitter: float = 0.1           # 首次触发在 ±jitter 比例内随机，避免整点齐发
    description: str = ""
    runner: Callable[[], Awaitable[Any]] | None = None

    next_run: float = 0.0
    running: bool = False
    runs: int = 0
    fails: int = 0
    disabled: bool = False
    last_error: str = ""
    last_elapsed: float = 0.0


class Scheduler:
    """按间隔跑 job 的小调度器（一个 asyncio 循环，够用就好）。"""

    def __init__(self, tick: float = 0.2) -> None:
        self.tick = float(tick)
        self._jobs: dict[str, Job] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # ── 注册 ──
    def add(self, job: Job, *, first_delay: float | None = None) -> Job:
        if first_delay is None:
            # 首次触发：半个间隔（+抖动），别让第一个任务干等一整个周期
            jitter = job.interval * max(0.0, job.jitter) * random.uniform(-1, 1)
            first_delay = max(0.01, job.interval * 0.5 + jitter)
        job.next_run = time.time() + first_delay
        self._jobs[job.id] = job
        log.info("注册后台任务 %s（每 %.0fs，超时 %.0fs）%s",
                 job.id, job.interval, job.timeout, job.description)
        return job

    def add_core_job(self, name: str, interval: float,
                     fn: Callable[[], Awaitable[Any]], timeout: float = 60.0,
                     description: str = "") -> Job:
        return self.add(Job(id="core:" + name, name=name, interval=interval,
                            timeout=timeout, description=description, runner=fn))

    def add_plugin_jobs(self, manager: Any) -> list[Job]:
        """把插件在 hello 里声明的 jobs 装进来。"""
        out: list[Job] = []
        for plugin, spec, proc in manager.iter_jobs():
            jid = "%s:%s" % (plugin, spec.get("name"))

            async def _run(plugin=plugin, name=spec.get("name")):
                return await manager.run_job(plugin, str(name))

            out.append(self.add(Job(
                id=jid, name=str(spec.get("name")), interval=float(spec.get("interval_sec", 3600)),
                timeout=float(spec.get("timeout", proc.timeout)),
                description=str(spec.get("description") or ""), runner=_run)))
        return out

    # ── 生命周期 ──
    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        log.info("后台任务调度启动：%d 个任务", len(self._jobs))

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    # ── 主循环 ──
    async def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                now = time.time()
                for job in list(self._jobs.values()):
                    if job.disabled or now < job.next_run:
                        continue
                    if job.running:
                        # 上一轮还没跑完：跳过这一轮，别堆积
                        log.warning("%s 上一轮还没结束，跳过这轮", job.id)
                        job.next_run = now + job.interval
                        continue
                    asyncio.create_task(self._run(job))
                await asyncio.sleep(self.tick)
        except asyncio.CancelledError:
            pass

    async def _run(self, job: Job) -> None:
        job.running = True
        job.next_run = time.time() + job.interval
        job.runs += 1
        t0 = time.time()
        try:
            if job.runner is None:
                raise RuntimeError("job 没有 runner")
            await asyncio.wait_for(job.runner(), timeout=job.timeout)
            job.fails = 0
            log.info("%s 完成（%.1fs）", job.id, time.time() - t0)
        except asyncio.TimeoutError:
            job.fails += 1
            job.last_error = "timeout"
            log.error("%s 超时（> %.0fs），已中断", job.id, job.timeout)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            job.fails += 1
            job.last_error = repr(e)
            log.exception("%s 失败（连续 %d 次）", job.id, job.fails)
        finally:
            job.running = False
            job.last_elapsed = time.time() - t0
            if job.fails >= MAX_FAILS and not job.disabled:
                job.disabled = True
                log.error("%s 连续失败 %d 次，已停用", job.id, job.fails)

    # ── 观测 ──
    def status(self) -> list[dict[str, Any]]:
        now = time.time()
        return [{"id": j.id, "interval": j.interval, "runs": j.runs, "fails": j.fails,
                 "disabled": j.disabled, "running": j.running,
                 "next_in": round(max(0.0, j.next_run - now), 1),
                 "last_elapsed": round(j.last_elapsed, 1),
                 "last_error": j.last_error}
                for j in self._jobs.values()]
