"""多通道管理：同时接 CLI、QQ（OneBot）等。

核心只认 Channel 抽象；谁负责哪类会话由 adapter 自己声明（`supports`）：
    cli     → {"local"}
    onebot  → {"group", "private"}

好处：
  · 服务器上 QQ 在线的同时，终端也能 `make chat`（同一个人格、同一份记忆）
  · 加 Discord/Telegram = 新写一个 adapter，核心不动
"""

from __future__ import annotations

import asyncio
import logging

from .channel import ChatRef, OnMessage, OutboundMessage, SendResult
from .pacing import PacingConfig, Pacer

log = logging.getLogger("channels")


class ChannelManager:
    def __init__(self, pacing: PacingConfig | None = None,
                 max_retries: int = 2, retry_backoff_sec: float = 0.8) -> None:
        self.pacer = Pacer(pacing)
        self.max_retries = int(max_retries)
        self.retry_backoff_sec = float(retry_backoff_sec)
        self._channels: list = []
        self._tasks: list[asyncio.Task] = []
        self._last: dict[str, object] = {}      # 会话 key → 最近一次收到消息的通道
        self._stop = asyncio.Event()

    # ── 注册 ──
    def add(self, channel) -> None:
        self._channels.append(channel)

    @property
    def channels(self) -> list:
        return list(self._channels)

    def by_name(self, name: str):
        return next((c for c in self._channels if getattr(c, "name", "") == name), None)

    # ── 路由 ──
    def route(self, chat: ChatRef):
        """挑通道：优先"这条会话上次从哪进来的"，其次按类型。

        （多个本地通道都能处理 local：cli / socket / tcp —— 必须回到原路，
          否则 tcp 里说的话会试图从没人的 socket 通道发出去。）
        """
        last = self._last.get(chat.key)
        if last is not None and chat.kind in getattr(last, "supports", set()):
            return last
        for c in self._channels:
            if chat.kind in getattr(c, "supports", set()):
                return c
        return self._channels[0] if self._channels else None

    def describe(self, chat: ChatRef) -> str:
        """这是哪儿 —— 由通道自己说，核心不猜。"""
        c = self.route(chat)
        fn = getattr(c, "describe", None) if c is not None else None
        return fn(chat) if fn else ""

    @property
    def self_account(self) -> str:
        for c in self._channels:
            acc = getattr(c, "self_account", "")
            if acc:
                return str(acc)
        return ""

    def idle(self, chat: ChatRef) -> None:
        """这个会话暂时没活了 —— 通道可以放心收尾（比如断开等待中的终端）。"""
        c = self.route(chat)
        fn = getattr(c, "idle", None) if c is not None else None
        if fn:
            fn(chat)

    def profile(self, chat: ChatRef) -> dict:
        """这个媒介的说话习惯 + 行内可用物（由通道自己声明，核心不猜）。"""
        c = self.route(chat)
        fn = getattr(c, "profile", None) if c is not None else None
        try:
            return dict(fn()) if fn else {}
        except Exception:
            log.exception("读取通道 profile 失败")
            return {}

    def scope_info(self, chat: ChatRef) -> dict:
        """这个会话是什么（kind/id/key/label/name）——由通道说，核心不猜。"""
        c = self.route(chat)
        fn = getattr(c, "scope_info", None) if c is not None else None
        if fn is None:
            return {"kind": chat.kind, "id": chat.id, "key": chat.key,
                    "label": "", "name": ""}
        try:
            return dict(fn(chat))
        except Exception:
            log.debug("scope_info 失败", exc_info=True)
            return {"kind": chat.kind, "id": chat.id, "key": chat.key,
                    "label": "", "name": ""}

    def identity(self, chat: ChatRef) -> dict:
        """她在这个会话里叫什么（name / aliases / account）——由通道说了算。

        真人在不同圈子有不同的称呼，这里就是那个"圈子里的名字"。
        """
        c = self.route(chat)
        fn = getattr(c, "identity", None) if c is not None else None
        if callable(fn):
            try:
                d = fn(chat)
                if isinstance(d, dict):
                    return {"name": str(d.get("name") or ""),
                            "aliases": [str(x) for x in (d.get("aliases") or [])][:8],
                            "account": str(d.get("account") or "")}
            except Exception as e:
                log.warning("通道 identity 出错：%r", e)
        acc = str(getattr(c, "self_account", "") or "") if c is not None else ""
        return {"name": acc, "aliases": [], "account": acc}

    def name_for(self, chat: ChatRef) -> str:
        c = self.route(chat)
        return str(getattr(c, "name", "")) if c is not None else ""

    def abilities_for(self, chat: ChatRef) -> set[str]:
        """这个会话能用哪些能力（决定哪些工具出现）。"""
        c = self.route(chat)
        return set(getattr(c, "abilities", set())) if c else set()

    # ── 生命周期 ──
    async def start_all(self, on_message: OnMessage) -> None:
        """启动所有通道并**阻塞**到任一通道结束或 stop_all()。

        任一通道退出就整体收工：CLI 里 /quit、或 OneBot 连不上直接退出让 systemd 重启，
        都比"半死不活地挂着"好。
        """
        for c in self._channels:
            name = getattr(c, "name", "?")
            log.info("启动通道：%s（支持 %s）", name, ",".join(sorted(getattr(c, "supports", set()))))
            self._tasks.append(asyncio.create_task(self._run(c, self._wrap(c, on_message))))
        stop_task = asyncio.create_task(self._stop.wait())
        done, _ = await asyncio.wait([*self._tasks, stop_task],
                                     return_when=asyncio.FIRST_COMPLETED)
        for t in self._tasks:
            if t in done and not t.cancelled() and t.exception():
                log.error("通道异常退出：%r", t.exception())
        stop_task.cancel()

    def _wrap(self, channel, on_message: OnMessage):
        """记住消息来自哪个通道，供 route() 回程用。"""
        async def _inner(msg):
            self._last[msg.chat.key] = channel
            await on_message(msg)
        return _inner

    async def _run(self, channel, on_message: OnMessage) -> None:
        name = getattr(channel, "name", "?")
        try:
            await channel.start(on_message)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("通道 %s 异常退出", name)

    async def stop_all(self) -> None:
        for t in self._tasks:
            t.cancel()
        for c in self._channels:
            try:
                await asyncio.wait_for(c.stop(), timeout=3)
            except Exception:
                pass
        self._tasks.clear()

    # ── 发送 ──
    def reset_pacing(self, chat: ChatRef) -> None:
        """新一轮开始：第一条不用等节奏。"""
        self.pacer.reset(chat.key)

    async def send(self, out: OutboundMessage) -> SendResult:
        c = self.route(out.chat)
        if c is None:
            return SendResult(ok=False, error="没有可用的通道", retryable=False)

        max_tries = max(1, int(getattr(self, "max_retries", 2)) + 1)
        backoff = float(getattr(self, "retry_backoff_sec", 0.8))
        last: SendResult | None = None
        for attempt in range(1, max_tries + 1):
            await self.pacer.before_send(out.chat.key, out.message.text)  # 节奏在这里统一生效
            res = await c.send(out)
            if res.ok:
                return res
            last = res
            # 只重试"明确失败 + 通道说值得再试"的；未确认的绝不重发（可能重复）
            if not getattr(res, "confirmed", True) or not getattr(res, "retryable", True):
                return res
            if attempt < max_tries:
                log.warning("发送失败（第 %d/%d 次），%.1fs 后重试：%s",
                            attempt, max_tries - 1, backoff, res.error or "未知原因")
                await asyncio.sleep(backoff)
                backoff *= 2
        return last or SendResult(ok=False, error="发送失败")
