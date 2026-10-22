"""DeepSeaBeast 核心：QQ(OneBot) 接入 + 门控管线 + LLM 工具调用 + 插件宿主。

分层（详见 docs/ARCHITECTURE.md）：
  onebot/   协议层：反向 WS 服务端、事件模型、action 调用
  pipeline/ 决策层：一条消息要不要回、回什么（每段一个函数，可单测）
  llm/      模型层：OpenAI 兼容 chat / chat_tools
  plugin/   扩展层：插件进程（JSON-RPC over unix socket）
"""

__version__ = "0.1.0"
