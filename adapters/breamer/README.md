# breamer 通道

把大肥鱼接进自己搭的 TCP 聊天室（`~/Projects/breamer` 的 `server.rs`）。

## 协议（对齐对方 server.rs，不是我设计的）

```
连上        → 服务器先发 `[request] Please enter the token: `
客户端      → 发一行口令 `<token>\n`
口令正确    → `[authed] Welcome, ...`
之后        → 每行文本广播给**其它**已登录用户：`[message] user<id>: <text>`
限速        → 每人 1 条/秒，超了记 strike，10 次封 IP 10 分钟
行尾        → `\n`（`\r` 服务器自己会去掉）
```

## 我们的翻译

| 方向 | 做法 |
|---|---|
| 收 | `[message] user3: 你好` → `InboundMessage(chat=room, sender=user3, text=你好)` |
| 发 | 核心的文字 → 一行 `\n`；多行压成一行（服务器按行广播） |
| 能力 | 只有 `text`（没有图片/表情/@/引用） |
| 会话 | `ChatRef.room(0, "breamer")` —— 多人在场但没有 @ 机制，门控按"房间"规则走 |

## 两个已知特性

1. **没有回执**：服务器不给自己回显，拿不到消息 id，所以 `SendResult.confirmed = False`
   （核心不会因此重发，避免重复）。
2. **每条消息都会问模型要不要接**：房间没有 @ 机制，门控把每条都当成"有人说话"。
   房间小、消息少时没问题，量大要加抽样。

## 配置

```toml
[adapters.breamer]
enabled = true
host = "127.0.0.1"
port = 6969
token_file = ".secrets/breamer.token"
room_key = "breamer"
room_name = "大肥鱼聊天室"
```
