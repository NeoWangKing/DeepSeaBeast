"""agent 层：把"像真人一样动手"做成工具（模型输出=思考，动作靠调用）。

- tools.py  工具实现（纯逻辑、可离线测）
- 注册与发送通道在 main.py（工具绑定会话，模型不能指定发到别的群）
"""
from . import llm, loop, tools, vision, websearch  # noqa: F401

__all__ = ["tools", "loop", "llm", "vision", "websearch"]
