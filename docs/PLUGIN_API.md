# 插件协议 v1

插件是**独立进程**，通过 stdin/stdout 上的**行分隔 JSON** 与核心通信。
这样插件可以用任何语言写（Python / Rust / Go / …），崩了也不会带走核心。

```
核心 ──stdin──▶ 插件
核心 ◀─stdout── 插件
（插件自己的日志写 stderr，stdout 只走协议）
```

## 1. 握手

核心拉起进程后，第一件事是 `hello`：

```json
// 核心 → 插件
{"id": 1, "method": "hello", "params": {
    "protocol": 1,
    "core_version": "0.1.0",
    "data_dir": "/opt/deepsea/data/plugins/persona"
}}

// 插件 → 核心
{"id": 1, "result": {
    "name": "persona",
    "version": "1.0",
    "protocol": 1,
    "capabilities": ["context"],
    "events": ["message.received", "reply.sent"],
    "tools": [
      {"name": "echo_say",
       "description": "把文本原样返回",
       "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                      "required": ["text"]},
       "requires": ["text"]}
    ]
}}
```

| 字段 | 说明 |
|---|---|
| `protocol` | **必须等于核心的版本**（当前 `1`）。不一致核心拒绝加载并报错 |
| `capabilities` | 声明实现哪些可选方法：`context` / `events` |
| `events` | 想订阅的事件名（见 §4）。没声明就收不到 |
| `tools` | 提供给模型的工具。`requires` 是所需通道能力（如 `face`），CLI 里没有就不会出现 |

## 2. 方法一览（核心 → 插件）

| 方法 | 何时调用 | 返回 |
|---|---|---|
| `hello` | 进程启动后一次 | 见上 |
| `ping` | 健康检查 | `{"pong": true, "uptime_sec": 12}` |
| `call_tool` | 模型调用了插件工具 | `{"ok": true, "data": ...}` 或 `{"ok": false, "error": "..."}` |
| `get_context` | 每轮组装上下文时（声明了 `context` 才算） | `{"persona": "...", "rules": [...], "knowledge": [...]}` |
| `event` | 订阅的事件发生时（**通知，无 id、不回包**） | — |
| `shutdown` | 核心退出时 | `{"ok": true}`，之后插件应自行退出 |

### call_tool 示例

```json
// 核心 → 插件
{"id": 7, "method": "call_tool", "params": {
    "name": "echo_say",
    "arguments": {"text": "你好"},
    "turn": {"id": "t-20261023-001", "scope": {"kind": "group", "id": 1109525246},
             "sender": {"id": 3245938285, "name": "灵其啊"}}
}}

// 插件 → 核心
{"id": 7, "result": {"ok": true, "data": {"echoed": "你好"}}}
```

失败就返回 `{"id": 7, "result": {"ok": false, "error": "参数缺失"}}`，
或者协议层错误 `{"id": 7, "error": "..."}`（核心会记一条 warning 并不再重试）。

## 3. 数据目录

核心在 `hello` 的 `params.data_dir` 里给出**该插件专属目录**，同时设置环境变量
`DEEPSEA_PLUGIN_DATA`（另有 `DEEPSEA_PLUGIN_NAME`）。

约定：
- 插件**只能**往这个目录写，不要碰 `data/memory` 等核心数据
- 目录在插件第一次启动前已创建，且已 `chown` 给运行用户
- 升级/回滚不会清空它

## 4. 事件（订阅即用）

在 `hello` 里声明 `events`，核心就会在事件发生时推 `event` 通知：

```json
{"method": "event", "params": {
    "event": "message.received",
    "data": {
      "scope": {"kind": "group", "id": 1109525246, "key": "g1109525246"},
      "sender": {"id": 3245938285, "name": "灵其啊"},
      "text": "这个群是干嘛的",
      "at_me": true,
      "ts": 1792700000.0
    }
}}
```

**目前的事件**：

| 事件 | 何时 | 数据 |
|---|---|---|
| `message.received` | 收到任意消息（**不管她回不回**） | scope / sender / text / at_me / ts |
| `reply.sent` | 她成功发出一条 | scope / text / ts |
| `turn.finished` | 一轮结束 | scope / sent（条数）/ decision（reply/silence） |

事件是**单向通知**：不需要回包，核心最多等 2 秒，超时/异常只记日志，不影响主流程。

## 5. 生命周期与容错

```
核心启动 → 创建 data_dir → 拉起进程 → hello（协议校验）
               │
               ├─ 调用失败 → 记 warning，本次调用返回错误给模型
               ├─ 连续失败 3 次 → 熔断：该插件暂停使用，日志报 ERROR
               ├─ 进程退出 → 下次调用前自动重启（最多 5 次）
               └─ 核心退出 → shutdown（3 秒超时）→ terminate
```

- 每个插件有独立 `timeout`（默认 20 秒），单次调用可再指定
- 同一插件并发调用默认上限 4
- 插件崩溃**不会**影响其它插件和核心

## 6. 最小实现（Python，完整可跑）

```python
import json, os, sys, time

START = time.time()
DATA = os.environ.get("DEEPSEA_PLUGIN_DATA", ".")

def handle(method, params):
    if method == "hello":
        return {"name": "my_plugin", "version": "1.0", "protocol": 1,
                "capabilities": ["tools"], "events": ["message.received"], "tools": []}
    if method == "ping":
        return {"pong": True, "uptime_sec": int(time.time() - START)}
    if method == "shutdown":
        return {"ok": True}
    if method == "call_tool":
        return {"ok": True, "data": {"got": params.get("arguments")}}
    raise ValueError("不认识的方法：%s" % method)

for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "event":       # 通知：不回复
        continue
    try:
        out = {"id": msg.get("id"), "result": handle(msg["method"], msg.get("params") or {})}
    except Exception as e:
        out = {"id": msg.get("id"), "error": str(e)}
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()
```

## 7. 参考实现

| 插件 | 位置 | 演示了什么 |
|---|---|---|
| `echo_plugin` | `plugins/echo_plugin/` | 工具、事件订阅、数据目录、shutdown |
| `persona` | `plugins/persona/` | `context` 能力、按作用域选人格卡 |
