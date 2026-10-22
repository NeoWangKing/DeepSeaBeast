"""配置：core.toml（核心）+ plugins/<name>.toml（插件各管各的）。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class OneBotConfig:
    """反向 WS：SnowLuma 主动连到我们。"""

    host: str = "127.0.0.1"
    port: int = 6200
    # 只接受这些 self_id（机器人 QQ），留空表示不校验
    allow_self_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class LogConfig:
    level: str = "INFO"


@dataclass(slots=True)
class CoreConfig:
    onebot: OneBotConfig = field(default_factory=OneBotConfig)
    log: LogConfig = field(default_factory=LogConfig)
    data_dir: str = "data"
    owner_ids: list[int] = field(default_factory=list)
    gate: dict = field(default_factory=dict)     # [gate] 段原样交给 pipeline 解析
    llm: dict = field(default_factory=dict)      # [llm] 段原样交给 llm 客户端解析
    persona: dict = field(default_factory=dict)   # [persona] text/file 覆盖默认人格


def load(path: str | Path = "core.toml") -> CoreConfig:
    """读 core.toml；文件不存在时用默认值（方便零配置跑起来）。"""
    p = Path(path)
    if not p.exists():
        return CoreConfig()
    raw = tomllib.loads(p.read_text(encoding="utf-8"))
    ob = raw.get("onebot") or {}
    lg = raw.get("log") or {}
    return CoreConfig(
        onebot=OneBotConfig(
            host=str(ob.get("host", "127.0.0.1")),
            port=int(ob.get("port", 6200)),
            allow_self_ids=[int(x) for x in (ob.get("allow_self_ids") or [])],
        ),
        log=LogConfig(level=str(lg.get("level", "INFO")).upper()),
        data_dir=str(raw.get("data_dir", "data")),
        owner_ids=[int(x) for x in (raw.get("owner_ids") or [])],
        gate=dict(raw.get("gate") or {}),
        llm=dict(raw.get("llm") or {}),
        persona=dict(raw.get("persona") or {}),
    )
