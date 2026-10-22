# DeepSeaBeast

QQ 群机器人核心。**QQ 接入用 SnowLuma（OneBot 反向 WS）**，核心自己实现：
门控管线、提示词装配、LLM 工具调用、插件宿主。

> 这是从零重写的版本（2026-10-22 起）。旧的 AstrBot 插件实现留在服务器上继续跑，
> 只作为参考，不再作为源。

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp core.example.toml core.toml          # 改端口/机器人 QQ
.venv/bin/python -m core core.toml      # 起反向 WS（默认 127.0.0.1:6200）

# 另开一个终端，用假客户端验证链路（不需要真 QQ）
.venv/bin/python tests/fake_onebot.py
```

预期：核心日志里出现 `收到消息 [g1109525246] 灵其啊(3245938285): '…'`。

## Membership

- `core/onebot/` 协议层：反向 WS 服务端、事件模型、action 调用（echo 关联）
- `core/pipeline/` 决策层：一条消息要不要回（后续按阶段拆）
- `core/llm/` 模型层：OpenAI 兼容 chat / chat_tools
- `core/plugin/` 扩展层：插件进程（JSON-RPC over unix socket）
- `plugins/` 各插件（可以先只有示例）
- `tests/` 假 OneBot 客户端 + 单测
