"""OneBot v11 反向 WebSocket 服务端。

SnowLuma（或任何 OneBot 实现）主动连到我们；同一条连接双向使用：
  · 它发过来 → 事件上报（post_type / meta_event）
  · 我们发过去 → API 调用（带 echo，等它的回包）

一个连接就够（单个机器人账号），所以多余的连接会被拒掉并记日志。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Awaitable, Callable

try:                                    # websockets >= 13 的新接口
    from websockets.asyncio.server import ServerConnection, serve
except ImportError:                     # 老版本兜底
    from websockets.server import WebSocketServerProtocol as ServerConnection  # type: ignore
    from websockets.server import serve  # type: ignore

from .event import Event

log = logging.getLogger("onebot")

EventHook = Callable[[Event, "OneBotServer"], Awaitable[None]]


class OneBotServer:
    """反向 WS 服务端 + action 调用器。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 6200,
                 allow_self_ids: list[int] | None = None) -> None:
        self.host = host
        self.port = port
        self.allow_self_ids = set(allow_self_ids or [])
        self._ws: ServerConnection | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._on_event: EventHook | None = None
        self._connected = asyncio.Event()
        self._server: Any = None
        self._probe = True
        self._tasks: set[asyncio.Task] = set()

    async def _probe_after_connect(self) -> None:
        """连上后调一次 get_login_info，把原始回包打出来（用来确认协议形态）。"""
        try:
            await asyncio.sleep(1.0)
            raw = await self.call_action("get_login_info", {}, timeout=8)
            log.info("探针 get_login_info 回包：%s", json.dumps(raw, ensure_ascii=False)[:200])
        except Exception as e:
            log.warning("探针失败：%r（如果这里超时，说明回包不带 echo）", e)

    # ── 生命周期 ────────────────────────────────────────────────
    def on_event(self, hook: EventHook) -> None:
        self._on_event = hook

    async def start(self) -> None:
        self._server = await serve(self._handle, self.host, self.port,
                                   max_size=16 * 1024 * 1024, ping_interval=30)
        log.info("OneBot 反向 WS 已在 ws://%s:%d 监听（等 SnowLuma 连上来）",
                 self.host, self.port)

    async def stop(self) -> None:
        """收工：先踢掉活动连接，再关服务端。

        顺序很重要——直接 wait_closed() 会等服务端的 handler 退出，
        而 handler 卡在读循环里、客户端又在等关闭握手，就会互相等死。
        """
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await asyncio.wait_for(ws.close(), timeout=2)
            except Exception:
                pass
        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=3)
            except Exception:
                pass

    async def wait_connected(self, timeout: float | None = None) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    @property
    def connected(self) -> bool:
        return self._ws is not None

    # ── 收包 ────────────────────────────────────────────────────
    async def _handle(self, ws: ServerConnection) -> None:
        if self._ws is not None:
            log.warning("已有连接在跑，拒绝重复连接（可能是 SnowLuma 重连太快）")
            await ws.close()
            return
        self._ws = ws
        self._connected.set()
        peer = getattr(ws, "remote_address", None)
        log.info("OneBot 连接已建立：%s", peer)
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except Exception:
                    log.warning("收到非 JSON 报文，忽略：%.80r", raw)
                    continue
                await self._dispatch(data)
        except Exception as e:
            log.warning("连接读循环异常：%r", e)
        finally:
            self._ws = None
            self._connected.clear()
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("OneBot 连接已断开"))
            self._pending.clear()
            log.info("OneBot 连接已断开")

    async def _dispatch(self, data: dict[str, Any]) -> None:
        if "retcode" in data or "echo" in data:          # API 调用的回包
            fut = self._pending.pop(str(data.get("echo")), None)
            if fut is not None and not fut.done():
                fut.set_result(data)
            else:
                log.info("未匹配的回包（没有 echo 或 echo 不认识）：%s",
                         json.dumps(data, ensure_ascii=False)[:200])
            return
        self_id = int(data.get("self_id") or 0)
        if self.allow_self_ids and self_id not in self.allow_self_ids:
            log.warning("忽略非白名单账号的事件 self_id=%s", self_id)
            return
        post = str(data.get("post_type") or "")
        if post in ("meta_event",):                        # 心跳/生命周期
            if data.get("meta_event_type") == "lifecycle":
                log.info("OneBot 生命周期事件：%s", data.get("sub_type"))
                if data.get("sub_type") == "connect" and self._probe:
                    self._probe = False
                    asyncio.create_task(self._probe_after_connect())
            return
        ev = Event.from_raw(data)
        if self._on_event is not None:
            # 关键：**不能 await**。处理函数里可能调 action 并等回包，
            # 而回包只能由这个读循环读 → await 会死锁（2026-10-22 真机踩到：
            # 消息发出去了，但我们等不到回包，30 秒超时后又重发一条）。
            task = asyncio.create_task(self._run_event(ev))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_event(self, ev: Event) -> None:
        try:
            await self._on_event(ev, self)          # type: ignore[misc]
        except Exception:
            log.exception("事件处理出错（不影响后续消息）")

    # ── 发调用 ──────────────────────────────────────────────────
    async def call_action(self, action: str, params: dict[str, Any] | None = None,
                          timeout: float = 30.0) -> dict[str, Any]:
        """调一个 OneBot 动作，等它的回包（echo 关联）。"""
        if self._ws is None:
            raise ConnectionError("OneBot 还没连上")
        echo = uuid.uuid4().hex
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[echo] = fut
        await self._ws.send(json.dumps({"action": action, "params": params or {},
                                        "echo": echo}, ensure_ascii=False))
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(echo, None)

    # ── 常用动作的薄封装（够 M1 用，后面按需加）───────────────
    async def send_group_msg(self, group_id: int, message: list[dict[str, Any]]) -> dict[str, Any]:
        return await self.call_action("send_group_msg",
                                      {"group_id": int(group_id), "message": message})

    async def send_private_msg(self, user_id: int, message: list[dict[str, Any]]) -> dict[str, Any]:
        return await self.call_action("send_private_msg",
                                      {"user_id": int(user_id), "message": message})
