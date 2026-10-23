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
    label: str = ""          # 中文名（能力清单里用）
    kind: str = "action"     # action / query / control

    def to_schema(self, abilities: set[str]) -> dict[str, Any] | None:
        if self.requires and not self.requires <= abilities:
            return None
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.parameters}}


class PluginManager:
    def __init__(self, llm: dict[str, Any] | None = None) -> None:
        self.llm = dict(llm or {})
        self._procs: dict[str, PluginProcess] = {}
        self.tools: dict[str, PluginTool] = {}
        self.capabilities: dict[str, set[str]] = {}     # 插件名 → 能力集合
        self.events: dict[str, set[str]] = {}           # 插件名 → 订阅的事件
        self.jobs: dict[str, list[dict[str, Any]]] = {}  # 插件名 → 声明的后台任务
        self._specs: dict[str, dict[str, Any]] = {}     # 插件名 → 原始配置

    # ── 装载 ──
    async def load(self, spec: dict[str, Any], base_dir: str = ".") -> None:
        """spec: {name, command:[...], cwd?, timeout?, tools?}"""
        name = str(spec.get("name") or "")
        argv = [str(x) for x in (spec.get("command") or [])]
        if not name or not argv:
            raise ValueError("插件配置要 name + command")
        cwd = str(spec.get("cwd") or base_dir)
        data_dir = str(spec.get("data_dir")
                       or (os.path.join(base_dir, "data", "plugins", name)))
        proc = PluginProcess(name, argv, cwd=cwd, timeout=float(spec.get("timeout", 20)),
                             data_dir=data_dir,
                             max_concurrency=int(spec.get("max_concurrency", 4)),
                             llm=self.llm)
        info = await proc.start()
        self._procs[name] = proc
        self._specs[name] = dict(spec)
        self.capabilities[name] = {str(x) for x in (info.get("capabilities") or [])}
        self.events[name] = set(proc.events)
        self.jobs[name] = [dict(j) for j in (info.get("jobs") or []) if j.get("name")]
        for t in (info.get("tools") or []):
            tool = PluginTool(name=str(t.get("name")), description=str(t.get("description") or ""),
                              parameters=t.get("parameters") or {"type": "object", "properties": {}},
                              plugin=name,
                              requires={str(x) for x in (t.get("requires") or [])},
                              label=str(t.get("label") or ""),
                              kind=str(t.get("kind") or "action"))
            if tool.name in ("decide", "send_message", "finish"):
                log.warning("插件 %s 想注册内置工具名 %s，已忽略（内置工具不可覆盖）",
                            name, tool.name)
                continue
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
                   timeout: float | None = None,
                   turn: dict[str, Any] | None = None) -> Any:
        t = self.tools.get(tool)
        if t is None:
            raise PluginError("没有这个插件工具：%s" % tool)
        proc = self._procs.get(t.plugin)
        if proc is None:
            raise PluginError("插件 %s 没在运行" % t.plugin)
        return await proc.call_tool(tool, arguments, timeout, turn)

    def has(self, tool: str) -> bool:
        return tool in self.tools

    # ── 事件 ──
    async def emit(self, event: str, data: dict[str, Any]) -> None:
        """把事件推给所有订阅者（单向，不影响主流程）。"""
        for name, evs in self.events.items():
            if event not in evs:
                continue
            proc = self._procs.get(name)
            if proc is None:
                continue
            await proc.notify(event, data)

    # ── 后台任务 ──
    def iter_jobs(self):
        for name, specs in self.jobs.items():
            proc = self._procs.get(name)
            if proc is None:
                continue
            for spec in specs:
                yield name, spec, proc

    async def run_job(self, plugin: str, job: str) -> Any:
        proc = self._procs.get(plugin)
        if proc is None:
            raise PluginError("插件 %s 没在运行" % plugin)
        return await proc.run_job(job)

    # ── 健康检查 ──
    async def health(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, proc in self._procs.items():
            pong = await proc.ping()
            out[name] = {"alive": proc._alive(), "pong": bool(pong.get("pong")),
                         "restarts": proc._restarts, "disabled": proc.disabled,
                         "fails": proc._fails, "events": sorted(self.events.get(name, ()))}
        return out

    async def close(self) -> None:
        for p in list(self._procs.values()):
            try:
                await p.shutdown()          # 先请它自己退，再兜底 kill
            except Exception:
                await p.close()
        self._procs.clear()
        self.tools.clear()

    # ── 上下文 provider ──
    def context_providers(self) -> list[Any]:
        """把声明了 context 能力的插件包成 ContextProvider。"""
        out: list[Any] = []
        for name, caps in self.capabilities.items():
            if "context" not in caps:
                continue
            spec = self._specs.get(name) or {}
            out.append(PluginContextProvider(
                manager=self, plugin=name,
                priority=int(spec.get("priority", 50)),
                can_persona=bool(spec.get("can_persona", False)),
                timeout=float(spec.get("timeout", 10))))
        return out

    # ── 健康检查（给 /health 用）──
    def status(self) -> dict[str, Any]:
        return {name: {"alive": p._alive(), "restarts": p._restarts}
                for name, p in self._procs.items()}


class PluginContextProvider:
    """把一个插件包装成 ContextProvider（核心只认贡献，不认插件细节）。"""

    def __init__(self, manager: "PluginManager", plugin: str, priority: int = 50,
                 can_persona: bool = False, timeout: float = 10) -> None:
        self.manager = manager
        self.plugin = plugin
        self.name = "plugin:" + plugin
        self.priority = int(priority)
        self.can_persona = bool(can_persona)
        self.timeout = float(timeout)

    async def contribute(self, bundle):
        from ..context import Contribution
        params = {
            "scope": {"kind": bundle.scope_kind, "id": bundle.scope_id,
                      "key": bundle.scope_key, "label": bundle.scope_label,
                      "name": bundle.scope_name},
            "sender": {"id": bundle.sender_id, "name": bundle.sender_name},
            "channel": {"name": bundle.channel_name},
            "message": (bundle.current.text if bundle.current is not None else ""),
        }
        try:
            res = await self.manager._procs[self.plugin].call("get_context", params, self.timeout)
        except Exception as e:
            log.warning("上下文插件 %s 调用失败：%r", self.plugin, e)
            return None
        if not isinstance(res, dict):
            return None
        return Contribution(priority=self.priority,
                            knowledge=[str(x) for x in (res.get("knowledge") or [])],
                            rules=[str(x) for x in (res.get("rules") or [])],
                            persona=str(res.get("persona") or ""))
