# 大肥鱼 · 运维手册（通用版）

> 给"拿到这份仓库后自己运维"的人看。某台服务器的具体细节（IP/群号/凭证/流水账）请写在你自己的 `SERVER_NOTES.md`，不要提交。

## 1. 组成与数据流

```
QQ 群消息 → 协议端(SnowLuma + LinuxQQ，或任意 OneBot v11) → AstrBot
                                                          ├→ qq_peak_gate 门控（本插件）
                                                          └→ 对话模型（如 DeepSeek）
本插件内部：
  main.py      门控 / 人格注入 / 记忆注入 / 资料库检索 / 海龟汤转发 / 表情包收发
  scoring.py   零 token「这条值不值得回」打分
  games/       玩法插件目录；games/turtle_soup/ = 海龟汤（判题走独立模型配置）
  memory/      群印象、人物档案（定时批处理）
  kb.py        本地资料库（data/kb 建索引，按相关度注入提示词）
  stickers.py  表情包收藏（识图打标、挑选发送、可同步 QQ 表情面板）
```

**核心设计**：所有花钱的动作都在门控之后；打分、海龟汤判题、表情包挑选都不占对话模型额度。

## 2. 目录与数据（哪些不入库）

| 路径 | 内容 | 入库 |
|---|---|---|
| `main.py` `scoring.py` `kb.py` `stickers.py` | 核心代码 | ✅ |
| `games/turtle_soup/` `memory/` | 子系统 | ✅ |
| `prompts/` | 提示词 + `personas/` 群人格卡 | 提示词 ✅ / 群卡 ❌ |
| `tools/` `tests/` `deploy/` `docs/` | 运维、测试、部署、文档 | ✅ |
| `.secrets/` | 模型 key | ❌ |
| `data/{memory,games,stickers,chatlog}/` `data/kb/*` | 记忆、对局、表情、日志、资料库内容 | ❌ |

## 3. 日常命令

```bash
qqbot-reload          # 热重载插件：不断连、不丢消息（有海龟汤对局会拒绝；--force 强制）
qqbot-restart         # 整机重启 AstrBot（断连约 15s，尽量少用）
qqbot-doctor          # 体检：服务、连接、key、定时器
qqbot-qq-reconnect    # 协议端没挂上 QQ 时重新挂载（重启后常见）
qqbot-sticker ...     # list/stats/add <图>/del <id>/tag/sync/faces/history/push
qqbot-kb ...          # build/search <词>/list/stats
python3 tests/gate_sim.py   # 离线测试台（60+ 场景，不联网、不碰 QQ）
```

## 4. 改动流程（务必照做）

1. 改代码 → `python3 tests/gate_sim.py` 全绿；
2. `git add -A && git commit`（隐私数据已 ignore）；
3. 部署 → **`qqbot-reload`**（别用整机重启更新插件）；
4. 看日志 `journalctl -u astrbot -n 50 | grep qq_peak_gate`；
5. 出问题回滚 `git reset --hard <上个 commit>` + `qqbot-reload`。

> 教训：整机重启那 15 秒里群里发的消息**不会补发**；改插件只热重载。

## 5. 主要开关（config.json）

| 键 | 说明 |
|---|---|
| `allowed_groups` / `allowed_self_id` | 群白名单 / 机器人 QQ（不在名单的群完全忽略） |
| `only_at_groups` | 这些群只有被 @ 才回 |
| `no_context_groups` / `no_log_groups` / `no_memes_groups` | 不注入上下文 / 不记日志 / 不注入黑话（隐私群） |
| `prompt_by_group` | 群 → 人格文件（`prompts/` 下文件名；`private` 管私聊） |
| `scoring` / `human_delay` / `split_reply` | 打分参数 / 拟人延迟 / 拆条发送 |
| `memory.*` | 记忆模型、注入策略、刷新频率 |
| `turtle.*` | 群、提问判定（`llm`/`at`/`at_quote`/`all`）、难度、冷却、AI 补题 |
| `stickers.*` | 收/发群、去重、频率、识图模型 |
| `kb.*` | 资料库：启用、top_k、相关度门槛、注入上限 |

## 6. 排障速查

| 症状 | 先看 |
|---|---|
| 完全不回 | 协议端是否连着（`ss -tn \| grep 6199`）→ `qqbot-qq-reconnect`；群是否在白名单 |
| 只回一部分 | 正常：非 @ 要过打分器，高峰期只回被 @ 的 |
| 改代码没生效 | 忘了 `qqbot-reload`；或 reload 被对局拒绝 |
| 判题/识图报错 | `.secrets/glm.key` 可读？额度用完？`turtle.judge` 模型配了？ |
| 表情包发不出 | `journalctl \| grep 表情包`：应"先落盘成本地文件再发"，直链会被 QQ 丢 |
| 重启后静默 | SnowLuma 未挂载 QQ 客户端 → `qqbot-qq-reconnect`（自带定时器每 3 分钟自愈） |
| 复读/阴阳 | 改 `prompts/system_prompt_*.txt`；或调低主动发言概率类参数 |

## 7. 成本经验值

- 门控、海龟汤判题、表情包挑选**不占对话模型**；
- 对话模型只在"判定要回"的消息上调用，短回复几百 token；
- 海龟汤判题每问约 0.7k token（一局 20~40 问）；AI 补题每道 1~2 万 token（含废稿）；
- 记忆批处理用便宜档模型，每天几万 token；
- 护栏：`max_auto_per_hour`、各功能 `max_per_hour`、海龟汤冷却、表情包频率。

## 8. 安全与隐私底线

1. 密钥只放 `.secrets/`（600），配置里写 `file:.secrets/xxx.key`，绝不入库/进日志；
2. 聊天内容只落本机 `data/`（已 ignore）；隐私群用 `no_context_groups` + `no_log_groups`；
3. 越权/隐私/骂人请求：人格卡要求"直接拒绝，不教不照做"；
4. 对外分享前跑一遍 `git ls-files`，确认没带出 `data/`、`.secrets/`、群人格卡。


## 提示词分层（做真人格优先读这一节）

```
人格层  prompts/personas/<群号>.txt 或 system_prompt*.txt   # 你是谁：性格/语气/梗/示例
通用补丁 prompts/personas/_common_patch.txt                 # AI 味黑名单 / 回复示例 / 群文化自适应
行为层  promptlib/sections.py（可被 prompts/behavior/<id>.txt 整段覆盖）
        # 安全 / 输出协议 / 反 AI 味 / 主体性 / 该说不该说 / 节奏 / 边界 / 表情策略 / 场景
```

- 拼装入口：`promptlib.build_system_prompt()`，在 `main.py` 的 `compact_context` 里把结果写进 `req.system_prompt`。
- 想改语气改人格层；想改"怎么干活"改行为层覆盖文件（下一条消息生效，不用重启）。
- 档位：`config.json` 的 `prompt.participation`（quiet/normal/active 改写【该说/不该说】）、
  `stickers.encourage` 0~3（改写【表情包策略】频率）、`prompt.disable_sections` 关段。
- 输出协议（模型侧约定）：`|||` 分条、整条 `[不说话]` 表示这条不发、`[表情:id]` 指名发某张收藏表情；
  解析在 `replyproto.py`，发送侧只做校验不再按长度硬切（兜底切分由 `split_reply.heuristic_fallback` 控制）。
- 自测：`python3 tests/proto_sim.py`（协议）、`python3 tests/flow_sim.py`（提示词+协议链路）。
