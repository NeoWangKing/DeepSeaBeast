"""插件宿主：**进程边界 + JSON-RPC（stdio）**。

为什么不用 in-process import：
  · 插件可以任意语言写（Python/Rust/Go…），只要能读写 stdin/stdout
  · 崩了不会带走核心；核心重启插件即可
  · 插件可以独立发版、独立测试

协议（每行一个 JSON）：
  核心 → 插件  {"id": 1, "method": "hello", "params": {...}}
  插件 → 核心  {"id": 1, "result": {"name": ..., "version": ..., "tools": [...]}}
  核心 → 插件  {"id": 2, "method": "call_tool", "params": {"name": ..., "arguments": {...}}}
  插件 → 核心  {"id": 2, "result": {...}}  或  {"id": 2, "error": "..."}
插件自己的日志请写 stderr —— stdout 是协议通道。
"""

from .manager import PluginManager
from .protocol import PluginError, PluginProcess, PluginTimeout

__all__ = ["PluginManager", "PluginProcess", "PluginError", "PluginTimeout"]
