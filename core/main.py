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
    _send_cfg = getattr(cfg, "send", {}) or {}
    channel = ChannelManager(pacing_cfg,
                             max_retries=int(_send_cfg.get("max_retries", 2)),
                             retry_backoff_sec=float(_send_cfg.get("retry_backoff_sec", 0.8)))
    log.info("发言节奏：%s（基础 %.1fs + %.3fs/字，封顶 %.1fs）",
             "开" if pacing_cfg.enabled else "关",
             pacing_cfg.base_sec, pacing_cfg.sec_per_char, pacing_cfg.max_sec)
    for c in channels:
        channel.add(c)

    plugins = PluginManager(llm={
        "base_url": llm_cfg.base_url, "model": llm_cfg.model,
        "api_key_file": llm_cfg.api_key_file, "api_key_env": llm_cfg.api_key_env,
        "timeout": llm_cfg.timeout,
    })
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
                      turn_cfg=TurnConfig.from_dict(getattr(cfg, "turn", {}) or {}),
                      todo_cfg=getattr(cfg, "todos", {}) or {})
    _p = cfg.persona or {}
    if _p.get("file"):
        try:
            runtime.persona = open(str(_p["file"]), encoding="utf-8").read().strip()
        except Exception as e:
            log.warning("读不到人格文件：%r", e)
    elif _p.get("text"):
        runtime.persona = str(_p["text"]).strip()

    todo_task = asyncio.create_task(runtime.watch_todos())
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
    ap.add_argument("config", nargs="?",
                    default=None, help="配置文件（默认自动找 core__custom__.toml / core.toml）")
    ap.add_argument("--list-scopes", action="store_true",
                    help="列出磁盘上留过痕迹的会话（排查用）")
    ap.add_argument("--reset-scope", default="", metavar="KEY",
                    help="清掉某个会话的上下文（s 开头的引用，或 g123/p123 原始 key）")
    ap.add_argument("--dry", action="store_true", help="配合 --reset-scope：只看会删什么")
    ap.add_argument("--channel", default="",
                    help="留空=按配置全开；填名字=只开那一个（如 cli），名字由 adapters/ 定义")
    a = ap.parse_args()

    # 运维命令：不启动核心，处理完就退出
    if a.list_scopes or a.reset_scope:
        from . import maintenance
        cfg = cfg_mod.load(a.config)
        if a.list_scopes:
            rows = maintenance.list_scopes(cfg.data_dir)
            if not rows:
                print("磁盘上还没有任何会话痕迹")
            for r in rows:
                print("%-18s %-10s %s" % (r["ref"], "/".join(r["what"]), r["label"]))
        if a.reset_scope:
            res = maintenance.reset_scope(cfg.data_dir, a.reset_scope, dry=a.dry)
            if res.get("error"):
                print(res["error"])
                return
            print("会话 %s：" % res["ref"])
            if not res["removed"]:
                print("  没有需要清的东西")
            for line in res["removed"]:
                print("  %s %s" % ("会删" if res["dry"] else "已删", line))
        return

    try:
        asyncio.run(run(a.config, a.channel))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
