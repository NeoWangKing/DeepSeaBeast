"""核心入口：选 adapter → 起运行时 → 等退出信号。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from . import __version__
from . import config as cfg_mod
from .channel_manager import ChannelManager
from .llm import LLMConfig
from .pipeline import GateConfig
from .plugin import PluginManager
from .runtime import Runtime

log = logging.getLogger("core")


def setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")


def build_channels(cfg, owner_ids: list[int]) -> list:
    """按配置装多个通道：CLI 和 QQ 可以同时在线。"""
    out = []
    ad = getattr(cfg, "adapters", {}) or {}

    if (ad.get("cli") or {}).get("enabled"):
        from adapters.cli import CliChannel
        out.append(CliChannel(user_name="本地用户",
                              owner_id=(owner_ids[0] if owner_ids else 0)))

    ob = ad.get("onebot") or {}
    if ob.get("enabled"):
        from adapters.onebot import OneBotChannel
        out.append(OneBotChannel(host=str(ob.get("host", cfg.onebot.host)),
                                 port=int(ob.get("port", cfg.onebot.port)),
                                 allow_self_ids=list(ob.get("allow_self_ids")
                                                     or cfg.onebot.allow_self_ids),
                                 owner_ids=owner_ids,
                                 bot_id=int(ob.get("bot_id", 0) or
                                            (cfg.onebot.allow_self_ids[0]
                                             if cfg.onebot.allow_self_ids else 0))))
    if not out:
        raise SystemExit("没有启用任何通道（[adapters.cli] 或 [adapters.onebot]）")
    return out


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
        if channel_name != "cli":
            raise SystemExit(2)
    if channel_name in ("cli", "onebot"):
        # 命令行显式指定时，只开那一个（本地开发最常用）
        ad = {channel_name: {"enabled": True}}
        cfg.adapters = ad
    channels = build_channels(cfg, owner_ids)
    channel = ChannelManager()
    for c in channels:
        channel.add(c)

    plugins = PluginManager()
    await plugins.load_all(getattr(cfg, "plugins", []) or [], base_dir=".")

    memory_cfg = getattr(cfg, "memory", {}) or {}
    runtime = Runtime(channel, gate, owner_ids, llm_cfg, plugins=plugins,
                      data_dir=cfg.data_dir,
                      budget_tokens=int(memory_cfg.get("budget_tokens", 6000)))
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
    start_task = asyncio.create_task(channel.start_all(runtime.on_message))
    stop_task = asyncio.create_task(stop.wait())
    done, pending = await asyncio.wait({start_task, stop_task},
                                      return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    await channel.stop_all()
    await plugins.close()
    if start_task in done and start_task.exception():
        log.error("channel 异常退出：%r", start_task.exception())
    log.info("已退出")


def main() -> None:
    ap = argparse.ArgumentParser(prog="deepsea-beast")
    ap.add_argument("config", nargs="?", default="core.toml")
    ap.add_argument("--channel", default="", choices=["", "cli", "onebot", "all"],
                    help="留空=按配置；cli/onebot=只开一个；all=按配置全开")
    a = ap.parse_args()
    try:
        asyncio.run(run(a.config, a.channel))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
