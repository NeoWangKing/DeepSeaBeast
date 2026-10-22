"""插件管理器：按配置拉起若干插件，收集它们声明的工具，负责分发调用。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from .protocol import PluginError, PluginProcess

log = logging.getLogger("plugin")


@dataclass(slots=True)
class PluginTool:
    """插件声明的一个工具（名字全局唯一，建议带插件前缀）。"""

    name: str
    description: str
    parameters: dict[str, Any]
    plugin: str
    requires: set[str] = field(default_factory=set)

    def to_schema(self, abilities: set[str]) -> dict[str, Any] | None:
        if self.requires and not self.requires <= abilities:
            return None
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.parameters}}


class PluginManager:
    def __init__(self) -> None:
        self._procs: dict[str, PluginProcess] = {}
        self.tools: dict[str, PluginTool] = {}

    # ── 装载 ──
    async def load(self, spec: dict[str, Any], base_dir: str = ".") -> None:
        """spec: {name, command:[...], cwd?, timeout?, tools?}"""
        name = str(spec.get("name") or "")
        argv = [str(x) for x in (spec.get("command") or [])]
        if not name or not argv:
            raise ValueError("插件配置要 name + command")
        cwd = str(spec.get("cwd") or base_dir)
        proc = PluginProcess(name, argv, cwd=cwd, timeout=float(spec.get("timeout", 20)))
        info = await proc.start()
        self._procs[name] = proc
        for t in (info.get("tools") or []):
            tool = PluginTool(name=str(t.get("name")), description=str(t.get("description") or ""),
                              parameters=t.get("parameters") or {"type": "object", "properties": {}},
                              plugin=name,
                              requires={str(x) for x in (t.get("requires") or [])})
            if tool.name in self.tools:
                log.warning("工具名重复：%s（%s 覆盖 %s）", tool.name, name,
                            self.tools[tool.name].plugin)
            self.tools[tool.name] = tool
        log.info("插件 %s 装载完成，提供 %d 个工具", name, len(info.get("tools") or []))

    async def load_all(self, specs: list[dict[str, Any]], base_dir: str = ".") -> None:
        for s in specs or []:
            try:
                await self.load(s, base_dir)
            except Exception:
                log.exception("插件 %s 装载失败（跳过）", s.get("name"))

    # ── 工具 ──
    def schemas(self, abilities: set[str]) -> list[dict[str, Any]]:
        out = []
        for t in self.tools.values():
            s = t.to_schema(abilities)
            if s:
                out.append(s)
        return out

    async def call(self, tool: str, arguments: dict[str, Any],
                   timeout: float | None = None) -> Any:
        t = self.tools.get(tool)
        if t is None:
            raise PluginError("没有这个插件工具：%s" % tool)
        proc = self._procs.get(t.plugin)
        if proc is None:
            raise PluginError("插件 %s 没在运行" % t.plugin)
        return await proc.call_tool(tool, arguments, timeout)

    def has(self, tool: str) -> bool:
        return tool in self.tools

    async def close(self) -> None:
        for p in list(self._procs.values()):
            await p.close()
        self._procs.clear()
        self.tools.clear()

    # ── 健康检查（给 /health 用）──
    def status(self) -> dict[str, Any]:
        return {name: {"alive": p._alive(), "restarts": p._restarts}
                for name, p in self._procs.items()}
