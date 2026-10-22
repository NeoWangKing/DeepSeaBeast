"""插件进程：启动、握手、调用、超时、崩溃重启。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

log = logging.getLogger("plugin")


PROTOCOL_VERSION = 1


class PluginError(RuntimeError):
    pass


class PluginTimeout(PluginError):
    pass


class PluginProcess:
    """一个插件子进程 + 行分隔 JSON 的请求/响应关联。"""

    def __init__(self, name: str, argv: list[str], cwd: str = "",
                 timeout: float = 20.0, env: dict[str, str] | None = None,
                 data_dir: str = "", max_concurrency: int = 4) -> None:
        self.name = name
        self.argv = list(argv)
        self.cwd = cwd or None
        self.timeout = float(timeout)
        self.env = env or None
        self.data_dir = data_dir
        self.info: dict[str, Any] = {}
        self.events: set[str] = set()
        self._sem = asyncio.Semaphore(max(1, int(max_concurrency)))
        self._fails = 0                 # 连续失败次数（熔断用）
        self._disabled = False
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._id = 0
        self._restarts = 0

    # ── 生命周期 ──
    async def start(self) -> dict[str, Any]:
        if self._proc is not None and self._proc.returncode is None:
            return self.info
        env = dict(os.environ)
        if self.env:
            env.update(self.env)
        if self.data_dir:
            os.makedirs(self.data_dir, exist_ok=True)
            env["DEEPSEA_PLUGIN_DATA"] = self.data_dir
        env["DEEPSEA_PLUGIN_NAME"] = self.name

        log.info("启动插件 %s：%s", self.name, " ".join(self.argv))
        self._proc = await asyncio.create_subprocess_exec(
            *self.argv, cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=None, env=env)
        self._reader_task = asyncio.create_task(self._read_loop())
        info = await self.call("hello", {"protocol": PROTOCOL_VERSION,
                                         "core_version": _core_version(),
                                         "data_dir": self.data_dir},
                               timeout=self.timeout)
        pv = int(info.get("protocol") or 0)
        if pv != PROTOCOL_VERSION:
            await self.close()
            raise PluginError("插件 %s 协议版本 %s 与核心 %d 不兼容"
                              % (self.name, pv, PROTOCOL_VERSION))
        self.info = info
        self.events = {str(x) for x in (info.get("events") or [])}
        log.info("插件 %s 就绪：v%s protocol=%d events=%s tools=%d",
                 self.name, info.get("version"), pv,
                 ",".join(sorted(self.events)) or "-", len(info.get("tools") or []))
        return self.info

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        p = self._proc
        self._proc = None
        if p is not None and p.returncode is None:
            try:
                p.stdin.close() if p.stdin else None
                p.terminate()
                await asyncio.wait_for(p.wait(), timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def _on_failure(self, method: str) -> None:
        """连续失败 3 次 → 熔断，避免一个坏插件拖慢每个回合。"""
        if method in ("hello", "shutdown", "ping"):
            return
        self._fails += 1
        if self._fails >= 3 and not self._disabled:
            self._disabled = True
            log.error("插件 %s 连续失败 %d 次，已熔断（重启核心或修好后才会恢复）",
                      self.name, self._fails)

    @property
    def disabled(self) -> bool:
        return self._disabled

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    # ── 健康检查 / 主动退出 / 事件通知 ──
    async def ping(self) -> dict[str, Any]:
        try:
            return await self.call("ping", {}, timeout=5)
        except Exception as e:
            return {"pong": False, "error": repr(e)}

    async def shutdown(self) -> None:
        if self._alive():
            try:
                await self.call("shutdown", {"reason": "core exit"}, timeout=3)
            except Exception:
                pass
        await self.close()

    async def notify(self, event: str, data: dict[str, Any]) -> None:
        """单向事件通知：不排队、不等回包、出错只记日志。"""
        p = self._proc
        if p is None or p.stdin is None or not self._alive():
            return
        try:
            payload = {"method": "event", "params": {"event": event, "data": data}}
            p.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
            await asyncio.wait_for(p.stdin.drain(), timeout=2)
        except Exception as e:
            log.debug("插件 %s 事件投递失败：%r", self.name, e)

    async def ensure(self) -> None:
        """没在跑就拉起来（崩溃重启）。"""
        if self._disabled:
            raise PluginError("插件 %s 已熔断" % self.name)
        if not self._alive():
            self._restarts += 1
            if self._restarts > 5:
                raise PluginError("插件 %s 反复崩溃（%d 次），先别用了"
                                  % (self.name, self._restarts))
            log.warning("插件 %s 不在运行，重启（第 %d 次）", self.name, self._restarts)
            await self.start()

    # ── 读循环 ──
    async def _read_loop(self) -> None:
        p = self._proc
        if p is None or p.stdout is None:
            return
        try:
            while True:
                line = await p.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8", "ignore"))
                except Exception:
                    log.debug("插件 %s 输出了非 JSON（忽略）：%.80r", self.name, line)
                    continue
                mid = msg.get("id")
                if mid is not None and mid in self._pending:
                    fut = self._pending.pop(mid)
                    if fut.done():
                        continue
                    if "error" in msg:
                        fut.set_exception(PluginError(str(msg["error"])))
                    else:
                        fut.set_result(msg.get("result"))
                elif msg.get("method"):
                    log.debug("插件 %s 主动通知：%s", self.name, msg.get("method"))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning("插件 %s 读循环异常：%r", self.name, e)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(PluginError("插件 %s 已断开" % self.name))
            self._pending.clear()

    # ── 调用 ──
    async def call(self, method: str, params: dict[str, Any] | None = None,
                   timeout: float | None = None) -> Any:
        await self.ensure() if method != "hello" else None
        p = self._proc
        if p is None or p.stdin is None:
            raise PluginError("插件 %s 没在运行" % self.name)
        self._id += 1
        mid = self._id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        payload = {"id": mid, "method": method, "params": params or {}}
        try:
            p.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
            await p.stdin.drain()
            res = await asyncio.wait_for(fut, timeout or self.timeout)
            self._fails = 0
            return res
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            self._on_failure(method)
            raise PluginTimeout("插件 %s 的 %s 超时（%.1fs）"
                                % (self.name, method, timeout or self.timeout))
        except PluginError:
            self._on_failure(method)
            raise
        finally:
            self._pending.pop(mid, None)

    async def run_job(self, job: str, timeout: float | None = None) -> Any:
        return await self.call("run_job", {"job": job}, timeout)

    async def call_tool(self, tool: str, arguments: dict[str, Any],
                        timeout: float | None = None,
                        turn: dict[str, Any] | None = None) -> Any:
        async with self._sem:
            return await self.call("call_tool", {"name": tool, "arguments": arguments,
                                                 "turn": turn or {}}, timeout)


def _core_version() -> str:
    try:
        from .. import __version__
        return str(__version__)
    except Exception:
        return "unknown"
