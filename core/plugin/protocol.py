"""插件进程：启动、握手、调用、超时、崩溃重启。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger("plugin")


class PluginError(RuntimeError):
    pass


class PluginTimeout(PluginError):
    pass


class PluginProcess:
    """一个插件子进程 + 行分隔 JSON 的请求/响应关联。"""

    def __init__(self, name: str, argv: list[str], cwd: str = "",
                 timeout: float = 20.0, env: dict[str, str] | None = None) -> None:
        self.name = name
        self.argv = list(argv)
        self.cwd = cwd or None
        self.timeout = float(timeout)
        self.env = env or None
        self.info: dict[str, Any] = {}
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._id = 0
        self._restarts = 0

    # ── 生命周期 ──
    async def start(self) -> dict[str, Any]:
        if self._proc is not None and self._proc.returncode is None:
            return self.info
        log.info("启动插件 %s：%s", self.name, " ".join(self.argv))
        self._proc = await asyncio.create_subprocess_exec(
            *self.argv, cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=None, env=self.env)
        self._reader_task = asyncio.create_task(self._read_loop())
        self.info = await self.call("hello", {"protocol": 1}, timeout=self.timeout)
        log.info("插件 %s 就绪：name=%s version=%s tools=%d",
                 self.name, self.info.get("name"), self.info.get("version"),
                 len(self.info.get("tools") or []))
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

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def ensure(self) -> None:
        """没在跑就拉起来（崩溃重启）。"""
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
            return await asyncio.wait_for(fut, timeout or self.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            raise PluginTimeout("插件 %s 的 %s 超时（%.1fs）"
                                % (self.name, method, timeout or self.timeout))
        finally:
            self._pending.pop(mid, None)

    async def call_tool(self, tool: str, arguments: dict[str, Any],
                        timeout: float | None = None) -> Any:
        return await self.call("call_tool", {"name": tool, "arguments": arguments}, timeout)
