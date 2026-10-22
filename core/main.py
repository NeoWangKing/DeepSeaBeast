"""核心入口：读配置 → 起反向 WS → 收事件 → 交给管线（M0 只打印）。"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from . import config as cfg_mod
from .onebot import Event, OneBotServer

log = logging.getLogger("core")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )


async def handle_event(ev: Event, server: OneBotServer) -> None:
    """M0：只把收到的事件打印清楚，用来确认链路通。"""
    if ev.post_type != "message":
        log.info("事件：post_type=%s", ev.post_type)
        return
    log.info("收到消息 [%s] %s(%s): %r | message_id=%s",
             ev.chat_key, ev.get_sender_name() or "?", ev.get_sender_id(),
             ev.message_str[:60], ev.message_id)
    # M1 会在这里接管线；先留个明确的钩子
    _ = server


async def run(config_path: str) -> None:
    cfg = cfg_mod.load(config_path)
    setup_logging(cfg.log.level)
    log.info("DeepSeaBeast 核心启动（data_dir=%s）", cfg.data_dir)

    server = OneBotServer(cfg.onebot.host, cfg.onebot.port, cfg.onebot.allow_self_ids)
    server.on_event(handle_event)
    await server.start()

    stop = asyncio.Event()

    def _ask_stop(*_: object) -> None:
        log.info("收到退出信号，准备收工")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _ask_stop)
        except NotImplementedError:      # Windows 上不支持
            pass

    await stop.wait()
    await server.stop()
    log.info("已退出")


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "core.toml"
    try:
        asyncio.run(run(path))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
