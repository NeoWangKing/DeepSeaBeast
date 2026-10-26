# 门控：要不要问她

> 一句话：**门控决定"这条消息值不值得问模型"，模型决定"该不该接、怎么接"。**

## 三个档位

| 档位 | 代码层行为 | 谁决定回不回 | 成本 |
|---|---|---|---|
| `strict` | 只放行"被叫到"的消息；其余直接丢，**不落任何记录** | 模型（只对 @ 的消息） | 极低 |
| `balanced` | 被叫到必问；没被叫时按**额度 + 场面打分**过滤 | 打分 + 模型 | 中 |
| `loose` | **每条都问模型**，代码不做频率拦截 | 模型（靠场面提示自己克制） | 高，有 `max_decide_per_hour` 兜底 |

配置：`[gate] default_preset`，按会话覆盖用 `[gate.chats."group:123"]`。

## 场面打分（balanced 用，loose 作为提示喂给模型）

| 信号 | 含义 | 默认权重 |
|---|---|---|
| `addressed` | 被叫到 | +3 |
| `question` | 直接问句 | +2 |
| `topic_overlap` | 和她刚参与的话题有词重叠 | +2 |
| `name_nearby` | 消息里提到她的名字/外号 | +2 |
| `unanswered` | 有人说话 2 分钟没人接（避免冷场尴尬） | +2 |
| `cold_room` | 群里安静 10 分钟后的第一条 | +1 |
| `dialogue` | 正在跟她对话的人 | +2 |
| `recent_speak` | 她 30 秒内刚说过 | −3 |
| `chatter` | 群里很吵（60 秒 > 20 条） | −2 |
| `budget` | 接近每小时 decide 上限 | −1 |

```
进 decide 的条件：分数 + 随机抖动(±1) ≥ score_threshold  且  发言额度 ≥ 1
```

**重点是分工**：门控只做廉价判断；"这话跟不跟她有关、该不该接"交给模型。
场面信号也会作为一行 `【场面】上一条已经没人接 200 秒了 · 群里正聊得很快`
写进提示词，让模型自己判断得体不得体。

## 发言额度（像"还有几次开口机会"）

```
上限 credit_max（默认 3 点）
每 credit_refill_min 分钟回 1 点（默认 15）
她每说一次扣 1 点（连续对话中扣 0.5）
额度见底 → 剩下的话只打分不开口，等回充
被叫到时额度见底也照回（人不会因为"配额"装没听见）
```

## 连续对话（状态机）

```
idle ──她开口──▶ engaged(peer, expires_at, turns)
                  │ 同一人继续说 → 续期 + 直接进 decide（不重新打分）
                  │ 换人 → 回到打分
                  │ 她连续沉默 2 次 → 结束
                  │ 超过 dialogue_max_turns（默认 6）→ 收尾
                  ▼
               过期（默认 120 秒）→ idle
```

## 功能开关（正交，跟档位无关）

| 开关 | 取值 | 说明 |
|---|---|---|
| `memory` | `off` / `scope` / `full` | **off = 不读也不写**：不发插件事件、不写 recent（豹群这类要求） |
| `history` | `true` / `false` | 上下文里带不带最近的聊天记录 |
| `tools` | `all` / `chat_only` / `none` | 工具可见性：none 只留说话+决定；chat_only 加表情 |
| `continuation` | `true` / `false` | 允不允许"她开口后接着聊" |
| `quiet_hours` | `["01:00-08:00"]` | 静默时段（支持跨夜） |

## 配置示例

```toml
[gate]
default_preset = "strict"        # 默认最保守
credit_max = 3
credit_refill_min = 15
score_threshold = 2.0
max_decide_per_hour = 30
dialogue_window_sec = 120
dialogue_max_turns = 6

[gate.chats."group:100000001"]   # 调试群：参与感强
preset = "loose"

[gate.chats."group:100000004"]   # 豹群：只认 @，完全不留痕
preset = "strict"
memory = "off"
history = false
```

旧键（`force_mode` / `speak_groups` / `only_when_addressed`）**仍能用**：
`speak_groups` → `loose`，`only_when_addressed` → `strict`，`force_mode=off` → 默认 `strict`。

## 加一个新信号只要三步

1. `core/signals.py` 的 `compute()` 里算出来（0/1，别调模型）
2. `WEIGHTS` 里给个默认权重
3. 想让人话提示也带上，就往 `hints` 里加一句

不需要动流水线，也不需要加全局开关。
