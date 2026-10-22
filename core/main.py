"""核心入口：选 adapter → 起运行时 → 等退出信号。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from . import __version__
from . import config as cfg_mod
from .llm import LLMConfig
from .pipeline import GateConfig
from .plugin import PluginManager
from .runtime import Runtime

log = logging.getLogger("core")


def setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")


def build_channel(name: str, cfg, owner_ids: list[int], bot_id: int):
    if name == "cli":
        from adapters.cli import CliChannel
        return CliChannel(user_name="本地用户", owner_id=(owner_ids[0] if owner_ids else 0))
    if name == "onebot":
        from adapters.onebot import OneBotChannel
        return OneBotChannel(host=cfg.onebot.host, port=cfg.onebot.port,
                             allow_self_ids=cfg.onebot.allow_self_ids,
                             owner_ids=owner_ids, bot_id=bot_id or
                             (cfg.onebot.allow_self_ids[0] if cfg.onebot.allow_self_ids else 0))
    raise SystemExit("未知 channel：%s（可选 cli / onebot）" % name)


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
    channel = build_channel(channel_name, cfg, owner_ids, 0)

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
    start_task = asyncio.create_task(channel.start(runtime.on_message))
    stop_task = asyncio.create_task(stop.wait())
    done, pending = await asyncio.wait({start_task, stop_task},
                                      return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    await channel.stop()
    await plugins.close()
    if start_task in done and start_task.exception():
        log.error("channel 异常退出：%r", start_task.exception())
    log.info("已退出")


def main() -> None:
    ap = argparse.ArgumentParser(prog="deepsea-beast")
    ap.add_argument("config", nargs="?", default="core.toml")
    ap.add_argument("--channel", default="cli", choices=["cli", "onebot"])
    a = ap.parse_args()
    try:
        asyncio.run(run(a.config, a.channel))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
