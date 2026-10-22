"""配置：core.toml（核心）+ plugins/<name>.toml（插件各管各的）。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class LogConfig:
    level: str = "INFO"


@dataclass(slots=True)
class CoreConfig:
    log: LogConfig = field(default_factory=LogConfig)
    data_dir: str = "data"
    owner_ids: list[int] = field(default_factory=list)
    gate: dict = field(default_factory=dict)     # [gate] 段原样交给 pipeline 解析
    llm: dict = field(default_factory=dict)      # [llm] 段原样交给 llm 客户端解析
    persona: dict = field(default_factory=dict)   # [persona] text/file 覆盖默认人格
    plugins: list[dict] = field(default_factory=list)   # [[plugins]] 数组
    memory: dict = field(default_factory=dict)   # [memory] budget_tokens 等
    adapters: dict = field(default_factory=dict)  # [adapters.cli] / [adapters.onebot]
    pacing: dict = field(default_factory=dict)    # [pacing] 发言节奏
    turn: dict = field(default_factory=dict)      # [turn] 回合预算
    jobs: dict = field(default_factory=dict)      # [jobs] 后台任务开关


def load(path: str | Path = "core.toml") -> CoreConfig:
    """读 core.toml；文件不存在时用默认值（方便零配置跑起来）。"""
    p = Path(path)
    if not p.exists():
        return CoreConfig()
    raw = tomllib.loads(p.read_text(encoding="utf-8"))
    _guard_top_level(raw, p)
    return CoreConfig(
        log=LogConfig(level=str((raw.get("log") or {}).get("level", "INFO"))),
        data_dir=str(raw.get("data_dir", "data")),
        owner_ids=[int(x) for x in (raw.get("owner_ids") or [])],
        gate=dict(raw.get("gate") or {}),
        llm=dict(raw.get("llm") or {}),
        persona=dict(raw.get("persona") or {}),
        plugins=list(raw.get("plugins") or []),
        memory=dict(raw.get("memory") or {}),
        adapters=dict(raw.get("adapters") or {}),
        pacing=dict(raw.get("pacing") or {}),
        turn=dict(raw.get("turn") or {}),
        jobs=dict(raw.get("jobs") or {}),
    )


TOP_LEVEL_KEYS = ("data_dir", "owner_ids")


def _guard_top_level(raw: dict, path: Path) -> None:
    """data_dir / owner_ids 写在某个 [table] 下面会被 TOML 当成表的成员，顶层读不到。

    这种错很隐蔽（她突然 "不认识" 主人），所以直接启动失败。
    """
    for section, val in raw.items():
        if not isinstance(val, dict):
            continue
        for k in TOP_LEVEL_KEYS:
            if k in val:
                raise SystemExit(
                    "配置错误：%s 里的 %s 写在 [%s] 表下面了，必须放在所有 [表] 之前"
                    % (path, k, section))
