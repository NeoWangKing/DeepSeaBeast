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


## agent 层速查（2026-10-02 新增，做「真人感」那一层）

```
agent/tools.py    工具实现（会话绑定，纯逻辑可离线测）
agent/loop.py     强制工具轮：tool_choice=required + 结果回传（说了话就收工）
agent/llm.py      OpenAI 兼容客户端（chat_tools / chat_text / chat_vision）
agent/vision.py   看图：DeepSeek 多模态（不用 GLM）
```
- 发送通道：`_transport_send` —— 有 event 走 `event.send`，定时（无 event）走 `context.send_message`
- 开关：`config.json` 的 `agent` 块
  - `enabled` 总开关；`loop_mode` + `loop_groups` 决定哪些会话走工具发言
  - `send_tools_groups` 决定哪些会话允许"用工具说话"
  - `tool_profiles` / `tool_profile_by_group` 按会话裁剪工具（豹群=lean，7 个）
  - `max_rounds` 每轮最多几轮工具调用；`max_calls` 单轮工具次数上限
- 定时：`schedule_wake(after_sec, say, reason, mode=say|think, every_sec)` + `cancel_wake`
  - say=到点直接发（0 token）；think=到点跑一次"主动轮"（可用全部工具）
- 兜底：发送口 `replyproto.sanitize()` 清内部标记（**保留 [QQ表情:…]**）；配 `data/agent_faults.json` 计数
- 体检：`python3 tools/agent_report.py [小时数]`
- 排障顺序：`qqbot-status`（故障计数/定时/表情库）→ `journalctl -u astrbot | grep qq_peak_gate` → `data/agent_faults.json`

## 发表情链路（2026-10-04 修）

她发图片表情只有两条路，都走 `_agent_send_sticker`（单独一条消息，不和文字同气泡）：

1. `send_sticker(sticker_id="s1790…")` —— 指名要哪张；id 从系统提示词末尾的【可用表情包】清单里拿
2. `send_sticker(mood="无语")` —— 不指名，按情绪/场景自己挑：`stickers.pick()` 用标签+备注匹配，
   情绪词带同义扩展（`_MOOD_SYN`：无语→尴尬/离谱/嫌弃…）；匹配不到就退化成"最久没用过的里面挑一张"

- 提醒：`_sticker_nudge()` 按 `stickers.nudge_prob`（默认 0.35，同一会话 8 分钟冷却）
  在当轮提示词里塞一句「这轮自然的话可以顺手配张表情」，不强制、由她自己判断
- 档位：`stickers.encourage` 0~3（越大越爱发），注入 `_sticker_rules` 那段行为层
- 为什么以前几乎不发：她调过 `send_sticker` 但**没带 id**（提示词只教了 `[表情:id]` 写法，
  和工具参数对不上），一次失败 + 另一次把 `max_calls` 用完 → 整轮一张也发不出去（见 10-04 02:22 日志）
- 排查：`journalctl -u astrbot | grep -E "发出表情|表情按"`；
  离线回归：`python3 tests/sticker_tool_sim.py`
- 注意：图片表情**没有**每小时上限；`stickers.max_per_hour` 只管旧的 `[表情:id]` 标记那条老路

## 上网搜索 / 读网页（2026-10-07 新增）

她多了两个工具：`web_search(query, count)` 和 `read_url(url)`。

- 搜索通道（`search.provider`）：
  1. `auto`（默认）—— 先用 AstrBot 里配了 key 的 provider（WebUI → 网页搜索，key 存 `cmd_config.json`
     的 `provider_settings.websearch_*_key`，我们直接复用 `astrbot/core/tools/web_search_tools.py` 的实现）
  2. 都没配 key 就退回 **Firecrawl 的免 key 搜索**（实测中英文都准，~1s；但没额度保障，挂了会记 `search_fail`）
  3. 想关掉：`search.enabled=false` 或 `agent.tools.search=false`
- 读网页：抓 HTML → 剥脚本/导航 → 纯文本，**带 SSRF 防护**（禁内网/本机/保留地址）、
  单页最多 1500KB、最多给 4000 字；`data/_web_cache` 缓存（搜索 10 分钟 / 正文 1 小时）
- 限额：`search.max_per_hour`（默认 20，按会话算）；失败计入 `agent_faults.json` 的 `search_fail`
- 提示词：行为层多了【没把握就查，别硬编】（只在 `search.enabled` 时注入）——先搜、关键那条点开原文、
  搜不到就说没找到、别念网址、查到的新事实用 memory_append 记一条
- 轮数：`agent.max_rounds` 2→3、`max_calls` 2→4（search→read→回答 至少 3 步；她说完话仍会立刻收工）
- 回归：`python3 tests/websearch_sim.py`（离线，含 SSRF / 正文提取 / 缓存 / schema）
- 想升级质量：在 WebUI 里给博查(bocha)或 Tavily 填个 key 即可，代码不用动

## 技能层 skills/（2026-10-07 新增）

把「需要时才用的长说明」从常驻提示词里搬出来：常驻只留一行索引，用到时她自己调
`use_skill(name)` 把整页拿进上下文（渐进式展开，跟 AstrBot 自带 Skills / Claude·Codex 的 SKILL.md 同格式）。

```
skills/<name>/SKILL.md     # 也支持 skills/<name>.md
---
name: turtle_soup          # 必填（没有 frontmatter/name 的文件不算技能）
description: 一句话，进索引
triggers: 触发词, 用逗号隔开（可选，也进索引）
---
正文：给模型的完整说明书，可以写很长
```

- 代码：`promptlib/skills.py`（扫目录/解析 frontmatter/`index_text()`/`load()`，30 秒缓存）
- 提示词：`promptlib/sections.py` 的 `_skills_index` → 常驻提示词里那段【技能（按需展开）】
  （只在有工具时注入；新技能只加文件，不用改代码）
- 工具：`agent/tools.py` 的 `use_skill(name)`（开关 `agent.tools.skills`）；展开会打日志
  `agent：展开技能 <name>（N 字）`，失败计 `skill_fail`
- 现有技能：`turtle_soup`（海龟汤怎么开/怎么判/红线）、`verify`（求证流程：先搜→读原文→交叉验证→给结论+来源→记笔记）
- 什么该做成技能：**按需**才需要的长说明（玩法手册、流程 SOP、某类任务怎么做）
- 什么不该：常驻行为规范（语气/分条/表情/沉默/安全/真人感）——那些必须每轮都可见，
  做成技能会因为"她没想起来加载"而失效，继续留在 `promptlib/sections.py`
- 回归：`python3 tests/skills_sim.py`

## 两个真事故的修复（2026-10-07）

**A. @ 她的消息被"连发合并"吞掉（10-06 22:55）**
现象：Na1ky0 引用她的话并 @ 她提问，她一条都没回。
根因：`连发合并`在拟人延迟结束时发现群里又有人说话，就把这条标成"交给后面那条合并"直接跳过；
  后面那条又撞上冷却 → 两条都没回。
修复：合并前先算 `_must_reply_now`（`_at_me` 或 `_quotes_me`），**必回的消息永不合并跳过**，日志会写
  「但这条 @了她/引用了她 → 不合并，照常处理」。

**B. 把"工具调用描述"当消息发出去了（10-06 19:16）**
现象：群里发来两张图，她发了一句
  「send_message 的调用参数里要发的话是「这表情怎么有点像我」。」
根因：① 看图轮沿用了带工具说明的系统提示词，模型就顺着写出了工具调用的描述；
  ② 看图轮没拿到【回】行时，会把"剩余正文"兜底当回复发出去。
修复（三层）：
  1. 看图轮改用**去掉工具说明/去掉"必须调 send_message"**的提示词（它那轮本来就不用工具）
  2. 兜底正文先过 `replyproto.looks_like_meta()`，像工具/协议描述就不发，并计 `meta_text_blocked`
  3. 发送口 `_agent_send` 统一拦截（所有路径都过），命中就记故障 `meta_text_blocked` 不发
`looks_like_meta()` 拦什么：`send_message`／`调用参数`／`参数里要发`／`工具调用`／`tool_call`／
`function_call`／`发消息工具`／JSON 样子的 `{name...}`；以及整条就是 `图：/回：/收：` 协议行。
故意**不拦**：单独的 `参数`、`arguments`（群里正常聊代码也会说到，误拦更烦）。
回归：`python3 tests/meta_guard_sim.py`（含事故原句做正例）

## 可查的事实问题：不许"不知道"打发（2026-10-07）

事故：10-07 10:29 Neo武神 @她问「那终末地下一个版本是什么时候更新」。
她手里 19 个工具（含 web_search），**一次没查**，直接回「不知道，我又不是鹰角内部人员」。

三层修法：
1. **提示词硬规则**（`promptlib/sections.py` 的 `_search_rules`，只在 search.enabled 时注入）：
   可查的事实（版本/更新时间、活动与开服时间、赛程比分、谁是冠军、价格、最新公告）上，
   「不知道」「我又不是内部人员」「等官方」**不算回答**——必须先查；只有喜好/看法/玩笑才免查。
2. **当轮提醒**（`_agent_loop_body`）：消息命中"可查问题"就地在当轮 user 里加一句
   「【这是可查的事实问题】…先调 web_search 查」。判定用 `agent/websearch.is_lookup_question()`（纯正则，不靠模型自觉）。
3. **查证兜底**（`_agent_loop_run`）：她这轮**说了话、且没说"不知道"类话、且没调 web_search/read_url**，
   但问题是可查的 → 记故障 `no_search_answer`，自动**补一轮**让她去查（最多补一次，日志：
   「查证兜底：可查的问题没查就答「不知道」→ 补一轮让她去查」）。

判定词表（`agent/websearch.py`）：
- 强命中（不问号也算问）：什么时候/啥时候/几号/哪天/多久/多少钱/谁赢/谁是冠军/版本更新/新版本/下一版/
  上线·开服·活动·发售时间/复刻/卡池/打谁/谁打谁/对手是谁/哪天打/几点打
- 弱命中（要带 ？/吗/呢/吧）：更新/上线/开服/公测/定档/赛程/对阵/比分/冠军/排名/积分/价格/发售/打折/最新/新消息/进展/公告
- 「装傻话」：不知道/不清楚/没听说/不了解/我哪知道/我又不是/不晓得/没有消息/问官方/等官方/关注官方/查不了

回归：`python3 tests/search_nudge_sim.py`（真实案例做正例）；实测同一问题搜索能拿到官方版本号与更新时间。

### 追加：她会"编造搜索过程"（10-07 10:35）

现象：@她「查一下终末地下一个版本什么时候更新」，她回
「你先去看官方公告，别信我瞎猜」+「我搜了下没找到，这会儿网也断着」。
**实际她一次工具都没调**（取证：`data/_web_cache` 在该时段无落盘、`agent_faults.json` 无 `search_fail`）。

为什么会这样：模型被要求"去查"但没真调工具时，会顺着编一个合理化借口（"网断了"）。
所以治它不能只靠提示词，得靠**可审计 + 硬兜底**：

1. **工具调用打 INFO**（main.py，`agent loop：本轮调用 → …`）：以前这类日志是 debug 级，
   出事只能靠缓存反推；现在每轮都会列出调了哪些工具，web_search/read_url/use_skill 还会打参数和结果前 150 字
2. **"没发生的事"也触发兜底**：`is_dontknow()` 词表加了 没找到/没查到/搜不到/搜了下/搜过了/网断了/连不上网…
   只要这轮**没真调** web_search/read_url → 记 `no_search_answer` 并补一轮让她去查
3. **提示词**加一条【不许编造工具发生的事】：没调 web_search 就不能说「我搜过了」；
   工具报错就说搜不了，也不许说「网断了」这种没验证过的话

## 治本：需要资料的问题**强制先查**（2026-10-07）

上一版靠"提示词提醒她查"＋"答不知道就补一轮"，实测不牢（她会跳过并编借口）。
这一版把"先查再答"变成**结构约束**：

- `_agent_loop_body`：消息命中"可查的事实问题"（`agent/websearch.is_lookup_question`）时，
  第一轮请求带 `tool_choice={"type":"function","function":{"name":"web_search"}}`
  → **她只能先调 web_search**，没有"跳过"这个选项；拿到结果后才进入正常回答轮
- `agent/loop.py`：`run(..., force_first="web_search")`；第一轮强制，第 2 轮起照旧 `required`；
  桩函数只收两个参数也能跑（向后兼容）
- 日志：「查证前置：判定这是可查的事实问题 → 第一轮强制 web_search」＋「agent loop：开始（…，强制先查）」
- 实测 DeepSeek 支持指定函数强制（`tool_choice` 传 dict）：强制后她给出的查询词是
  「终末地 下一个版本 更新时间」，很合理
- 提示词里加了一个**照做的例子**（few-shot 比抽象规则管用）：
  问版本更新时间 → 先 web_search → 关键那条 read_url → 回「查了下，官方公告写的是 X 月 X 日」

保留的软路径（没被判定成"可查问题"时）：她自己判断要不要查；答"不知道/没找到/网断了"但没真查
→ 记 `no_search_answer` 并补一轮（见上一节）。

可选的下一步（还没做）：
1. 判定换成"小模型判一次"（`deepseek-flash`，几十 token）：覆盖正则漏掉的信息需求，代价是每轮多 ~1s
2. 本地资料库/记忆命中充分时跳过强制搜索（省时间）；查到的新事实让她 `memory_append` 存下来，二次提问就不用再搜
3. 高事实密度回答（带具体日期/版本号/比分）加"断言闸门"：本轮没查就要求她标注不确定
