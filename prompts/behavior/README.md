# 行为层覆盖（可选）

系统提示词分两层：

| 层 | 放哪 | 管什么 | 怎么改 |
|---|---|---|---|
| **人格层** | `prompts/personas/<名>.txt` | 你是 **谁**：身份、性格、说话风格、梗、雷区、回复示例 | 直接改文本，改完 `qqbot-reload`（或下一条消息生效，取决于缓存） |
| **行为层** | 本目录（代码内置 `promptlib/sections.py`） | 你 **怎么干活**：安全、输出协议、节奏、边界、引用规则、表情策略 | 用本目录同名 `<id>.txt` 覆盖整段；或在 `config.json` 的 `prompt.disable_sections` 里关掉 |

## 可覆盖的段 id（顺序即拼装顺序）

```
identity        身份句（随 bot_name）
safety          安全规则
protocol        输出协议：||| 分条 / [不说话] / [表情:id]
anti_ai         反 AI 味
subjectivity    保持主体性
speak_or_not    该说/不该说（首行随 prompt.participation 档位改写）
not_a_queue     群聊不是客服队列
human_rhythm    像真人一样（漏看/晚回/可以不回）
not_moderator   不要当群管家
quote_and_at    引用与点名
memory_notes    关于"记忆"和资料
sticker_rules   表情包策略（首行随 stickers.encourage 0~3 改写）
scene           QQ 场景规则（按 caps：看图/联网/资料库 条件增删）
report_ban      发送与汇报禁令
```

## 用法

在本目录放 `ant_ai.txt` 之类的文件即可整段替换，例如 `prompts/behavior/anti_ai.txt`。
只想关掉某段、不重写内容，就在 `config.json` 里：

```json
"prompt": {
  "disable_sections": ["not_moderator"],
  "participation": "normal",
  "persona_patch": "personas/_common_patch.txt",
  "extra": ""
}
```

## 改完怎么生效

- 覆盖文件是**每次请求前实时读取**的（提示词不走缓存文件），改完下一条消息就生效。
- 只改了代码里的默认段落（`promptlib/sections.py`）才需要 `qqbot-reload`。
- 排查：开着 `dump_prompt` 时，`data/prompt-dump.jsonl` 里能看到最终拼好的 system prompt 与各段字数。
