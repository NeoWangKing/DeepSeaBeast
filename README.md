# DeepSeaBeast

QQ 群机器人**核心**。QQ 接入用 SnowLuma（OneBot v11 反向 WS），核心自己实现
门控管线、上下文装配、LLM 工具调用与插件宿主。

> 2026-10-22 起从零重写；旧的 AstrBot 插件实现已停用（备份在服务器 `/opt/backups/`）。
> **M5 已上线**：服务器上跑的是本仓库的代码。

## 为什么重写

旧实现是 AstrBot 插件：`main.py` 4600 行里混着门控、提示词、工具、玩法，
进程内存 779MB 里 97% 是 AstrBot 框架。新核心自己实现协议与管线：
**16MB 内存、启动 1 秒、没有热重载竞态**，玩法/人格/记忆全部交给插件。

## 结构

```
core/               内核（协议无关）
  channel.py        Channel 抽象：InboundMessage / OutboundMessage / abilities
  runtime.py        收 → 管线 → 上下文装配 → 工具循环 → 发
  pipeline/         门控四阶段：白名单 / 频率 / 决定 / 连发合并
  agent.py + tools.py  工具调用循环（decide / send_message / finish）
  context.py        ContextBundle：人格/规则/self 记忆/scope 记忆/场景/最近
  memory.py         分层记忆（self / scope / recent）
  prompt.py         渲染（顺序固定，利于前缀缓存）
  llm/              OpenAI 兼容客户端（chat / chat_tools）
  plugin/           插件宿主（stdio JSON-RPC，握手/超时/崩溃重启）
  scope.py          作用域（local / private / group）
adapters/           前端通道
  cli/              命令行（本地开发、也是"核心不认识 QQ"的证明）
  tcp/              telnet 风格（127.0.0.1:4000，SSH 隧道即可用）
  socket/           unix socket（本机脚本接入）
  onebot/           SnowLuma / NapCat / Lagrange 等 OneBot 实现
plugins/            能力插件（示例 echo_plugin）
deploy/             部署文档与 systemd 单元
```

## 常用命令

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp core.example.toml core.toml      # 端口 / 机器人 QQ / LLM key

make chat        # 命令行直接聊（不需要 QQ）
                 # 也可以同时接 QQ：core.toml 里 [adapters.*] 都 enabled
make selfcheck   # 一体自测：核心 + 假 SnowLuma 往返
make test        # 全部自测（管线/工具/插件/记忆/模型）
make bot         # OneBot 模式（等 SnowLuma 连上来）
make telnet      # 连服务器上运行中的核心（telnet 风格，同一个人格与记忆）
```

## 里程碑

| | 状态 |
|---|---|
| M0 收事件（反向 WS + 事件模型） | ✅ |
| M1 能回消息（带引用） | ✅ |
| M2 门控管线（白名单/频率/决定/合并） | ✅ |
| M3 LLM + 工具调用（decide / send_message / finish） | ✅ |
| M4 插件协议（stdio JSON-RPC） | ✅ |
| M4.5 分层记忆 + 上下文装配 + 缓存命中日志 | ✅ |
| **M5 真机上线**（SnowLuma 保留，AstrBot 停用） | ✅ 2026-10-22 |
| M6 多通道（CLI + QQ 同时在线） | ✅ |
| M7 人格式记忆策略 / 插件生态 | ⬜ |

## 已知未做

- 人格卡插件（现在只有 `[persona]` 里 5 行极简人格）
- 记忆迁移（旧实现的群印象/成员档案/黑话/表达库）
- 能力补齐：表情包、发文件、看图、联网搜索
- 群聊之外的通道（Discord/Telegram/HTTP）
