"""核心入口：选 adapter → 起运行时 → 等退出信号。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from . import __version__
from . import config as cfg_mod
from adapters import build_channels      # 装配层：唯一知道有哪些通道的地方
from .capability import build_report
from .channel_manager import ChannelManager
from .llm import LLMConfig
from .pacing import PacingConfig
from .scheduler import Scheduler
from .turn import TurnConfig
from .pipeline import GateConfig
from .plugin import PluginManager
from .runtime import Runtime

log = logging.getLogger("core")


def setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")


async def run(config_path: str, channel_name: str) -> None:
    cfg = cfg_mod.load(config_path)
    setup_logging(cfg.log.level)
    log.info("DeepSeaBeast 核心 v%s 启动（channel=%s，data_dir=%s）",
             __version__, channel_name, cfg.data_dir)

    owner_ids = [int(x) for x in getattr(cfg, "owner_ids", []) or []]
    gate = GateConfig.from_dict(cfg.gate or {})
    llm_cfg = LLMConfig.from_dict(getattr(cfg, "llm", {}) or {})

    # 启动自检：连不上模型的话，宁可启动失败让人看见，也别静默不说话
    _key = llm_cfg.resolve_key()
    if not _key:
        log.error("LLM key 读不到：api_key_file=%r / api_key_env=%r",
                  llm_cfg.api_key_file, llm_cfg.api_key_env)
        raise SystemExit(2)
    channels = build_channels(cfg, owner_ids, only=channel_name)
    pacing_cfg = PacingConfig.from_dict(getattr(cfg, "pacing", {}) or {})
    channel = ChannelManager(pacing_cfg)
    log.info("发言节奏：%s（基础 %.1fs + %.3fs/字，封顶 %.1fs）",
             "开" if pacing_cfg.enabled else "关",
             pacing_cfg.base_sec, pacing_cfg.sec_per_char, pacing_cfg.max_sec)
    for c in channels:
        channel.add(c)

    plugins = PluginManager()
    await plugins.load_all(getattr(cfg, "plugins", []) or [], base_dir=".")

    memory_cfg = getattr(cfg, "memory", {}) or {}
    scheduler = Scheduler()
    if getattr(cfg, "jobs", {}).get("enabled", True):
        scheduler.add_plugin_jobs(plugins)

    for line in build_report(channels, list(plugins.tools.values())).lines():
        log.info(line)

    runtime = Runtime(channel, gate, owner_ids, llm_cfg, plugins=plugins,
                      data_dir=cfg.data_dir,
                      budget_tokens=int(memory_cfg.get("budget_tokens", 6000)),
                      turn_cfg=TurnConfig.from_dict(getattr(cfg, "turn", {}) or {}))
    _p = cfg.persona or {}
    if _p.get("file"):
        try:
            runtime.persona = open(str(_p["file"]), encoding="utf-8").read().strip()
        except Exception as e:
            log.warning("读不到人格文件：%r", e)
    elif _p.get("text"):
        runtime.persona = str(_p["text"]).strip()

    stop = asyncio.Event()

    def _ask_stop(*_: object) -> None:
        log.info("收到退出信号")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _ask_stop)
        except NotImplementedError:
            pass

    # channel.start() 自己结束（比如 CLI 里 /quit）也算收工
    await scheduler.start()
    start_task = asyncio.create_task(channel.start_all(runtime.on_message))
    stop_task = asyncio.create_task(stop.wait())
    done, pending = await asyncio.wait({start_task, stop_task},
                                      return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    await channel.stop_all()
    await scheduler.stop()
    await plugins.close()
    if start_task in done and start_task.exception():
        log.error("channel 异常退出：%r", start_task.exception())
    log.info("已退出")


def main() -> None:
    ap = argparse.ArgumentParser(prog="deepsea-beast")
    ap.add_argument("config", nargs="?", default="core.toml")
    ap.add_argument("--channel", default="",
                    help="留空=按配置全开；填名字=只开那一个（如 cli），名字由 adapters/ 定义")
    a = ap.parse_args()
    try:
        asyncio.run(run(a.config, a.channel))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
