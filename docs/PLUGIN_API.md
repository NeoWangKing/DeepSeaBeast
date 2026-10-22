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
    "data_dir": "/opt/deepsea/data/plugins/personas"
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

`get_context` 的入参：

```json
{"scope": {"kind": "group|private|local", "id": <平台给的 id>, "key": "<稳定唯一键>",
           "label": "<人话类别，如 群聊/私聊/终端>", "name": "<平台显示名，可能为空>"},
 "sender": {"id": 3245938285, "name": "灵其啊"},
 "channel": {"name": "onebot"}}          // 这条消息从哪个 adapter 来的
```

`channel.name` 让插件能按"通道种类"选不同的人格卡（QQ / 终端 / 脚本接口…）。

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

### 插件不直接碰通道

插件**不能**自己发消息——它返回"要做的动作"，由核心按通道能力执行：

```json
{"id": 7, "result": {"ok": true, "data": {
   "send": [
     {"kind": "text", "text": "日志我放这了"},
     {"kind": "file", "path": "/opt/deepsea/data/plugins/x/a.log", "name": "a.log"}
   ]
}}}
```

核心逐条执行：
- 通道支持（有 `file` 能力）→ 真的发出去
- 通道不支持 → **原样把"做不到"告诉模型**，由它用语言兜底（**不静默降级**）

这样插件不需要知道对面是 QQ 还是终端，也不会因为"某通道不支持"而写一堆分支。

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

## 6. 后台任务（jobs）

核心会在**没人说话的时候**按间隔调插件——这是"她主动做事"的唯一入口。

在 `hello` 里声明：

```json
{"jobs": [
  {"name": "refresh_impression", "interval_sec": 3600, "timeout": 120,
   "description": "每小时重写本群印象"}
]}
```

核心按时调用：

```json
{"id": 21, "method": "run_job", "params": {"job": "refresh_impression"}}
// → {"id": 21, "result": {"ok": true, "data": {"updated": 3}}}
```

**核心保证的三件事**（插件不用自己处理）：

| 保证 | 说明 |
|---|---|
| 不并发 | 同一 job 上一轮没跑完，这一轮直接跳过（日志有 warning） |
| 有超时 | 超过 `timeout` 就取消这一轮，记一次失败 |
| 会熔断 | 连续失败 3 次 → 该 job 停用并报 ERROR，不影响消息处理 |

首次触发是「半个间隔 + 抖动」，避免多个插件在整点同时开跑。
任务的结果建议写进 `$DEEPSEA_PLUGIN_DATA`，不要碰核心的 `data/memory`。

## 7. 最小实现（Python，完整可跑）

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

## 9. 通道 profile（给 adapter 作者看的）

adapter 除了 `abilities`，还要声明**这个媒介的说话习惯**和**行内可用物**：

```python
def profile(self) -> dict:
    return {
        "style": "这是 QQ。短句为主，一句话一条；不要用 markdown。",
        "inline": {"faces": ["微笑", "呲牙", ...],   # 平台全部表情名（不注入提示词，给工具查）
                   "mention": True, "markdown": False},
        "limits": {"max_chars": 4500},
    }
```

- **行内**（能混在文字里发的）才需要列清单：表情、@人
- **附件/动作**（图片/文件/语音/戳一戳）走独立工具，各自声明 `requires`
- **Unicode 表情和颜文字是纯文本**，不需要声明，原样收发

核心会把 top-N（按这个群的使用频率统计）写进提示词，其余靠 `list_faces` 工具查。

## 8. 参考实现

| 插件 | 位置 | 演示了什么 |
|---|---|---|
| `echo_plugin` | `plugins/echo_plugin/` | 工具、事件订阅、**后台任务**、数据目录、shutdown |
| `personas` | `plugins/personas/` | `context` 能力、按作用域选人格卡 |
| `memory` | `plugins/memory/` | `context` + `events` + `jobs`：记忆空间（全局 + 每会话） |
