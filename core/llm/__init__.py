"""模型层：OpenAI 兼容的 chat / chat_tools（不绑定任何一家厂商）。"""

from .client import LLMConfig, LLMResponse, ToolCall, chat, chat_tools

__all__ = ["LLMConfig", "LLMResponse", "ToolCall", "chat", "chat_tools"]
