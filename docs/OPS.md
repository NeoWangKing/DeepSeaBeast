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

### 三件套补齐（2026-10-07）

1. **小模型判一次"要不要查"**（`agent/websearch.needs_lookup()`）
   顺序：规则强命中 → 直接 yes（不花钱）→ 不像问句 → 空（不花钱）→ 其余问句才让小模型判一次。
   结果缓存 30 分钟；判定调用按会话限 `search.judge_max_per_hour`（默认 30/小时）；模型 `search.judge_model`（空=主模型）。
   实测：`现在终末地主流阵容是啥`/`明天天气如何` → 要查（0.7~0.8s）；`你会唱歌吗`/`这个任务咋做` → 不用查；
   `我在摸鱼` → 免判。问句词表含 啥/咋/如何/怎样/哪些/几个/几号…（"是啥"这类以前会漏）。
2. **本地资料/记忆够硬就不强制查**（`should_force()` + `search.skip_if_evidence`，默认 0.34）
   证据 = max(资料库最高相关度, 记忆块与问题的重叠度)；够了就**不强制**，交给她自己判断（先回忆再查，像真人）。
   日志：`查证前置：判定=yes 本地证据=0.42（阈值0.34）→ 不强制，交给她自己判断`
3. **查证结论写回记忆**（`remember_note()` + `search.remember`）
   本轮真调了 `web_search` 且她说了话 → 把「他问过 X，我查证后回：Y」写进 `data/agent_memory/<会话>.jsonl`
   （kind=topic，按会话限 `remember_max_per_hour`=4、近 60 行内去重）。记忆每轮都会注入 → 同类问题第二次不用再搜。
   日志：`查证记忆：已记下「…」`

新增配置（`search` 块）：`smart_judge` / `judge_model` / `judge_max_per_hour` / `skip_if_evidence` /
`remember` / `remember_max_per_hour`。
回归：`python3 tests/search_nudge_sim.py`（含判定缓存、证据阈值、记忆条目、强制首轮等 20+ 用例）。

## 事故：回调没接线（2026-10-07 10:43）

现象：@她查版本更新时间，她回「搜不了，这轮网断了」+「等会儿你再问我一遍，我给你查」。
日志真相：

```
agent loop：本轮调用 → web_search、web_search、send_message
agent loop：web_search({'query': '明日方舟 终末地 下一个版本更新时间 官方公告'}) → 没查：这个会话现在不能联网搜索
```

**她其实调了（还调了两次，中英文各一遍），但工具一律回"不能联网搜索"**——因为
`_agent_callbacks` 里定义了 `_web_search / _read_url / _use_skill` 三个回调，却忘了登记进 `cbs` 字典，
`Tools.cb.get("web_search")` 取不到 → 直接返回那句"不能联网搜索"。所以：
- 她的"搜不了"是真的（工具确实报不能搜），只有"网断了"是脑补
- `web_search`/`read_url`/`use_skill` 三个功能从加上线起就一直没用过

修复：
1. `cbs` 里补上 `web_search` / `read_url` / `use_skill` 三个键
2. **接线自检**：`agent/tools.py` 用正则自曝 `TOOL_CB_KEYS`（本文件里 `self.cb.get("X")` 用到的所有键），
   `_agent_callbacks` 在 `return cbs` 前对一遍，缺了就记故障 `callback_missing`（只报一次）
3. **回归测试** `tests/callback_wiring_sim.py`：把 tools.py 需要的回调键和 main.py 提供的键对比，
   少一个就 FAIL（这个测试当场就抓到了缺 read_url/use_skill/web_search）
4. 清掉那条被写进记忆的错误"查证结论"（「我查证后回：搜不了，这轮网断了」）

## 配套：查资料别把轮数用光（2026-10-07）

全链路仿真（真模型+真回调+真搜索，发送口用桩接住）暴露的问题：她会连着搜 6 次、把 3 轮用光，
**最后一句都没说**。补了两道闸：
1. `search.max_per_turn`（默认 3）：一轮最多查/读 3 次，超了工具直接回
   「这轮已经查了 N 次了（上限 3）：别继续搜，直接拿查到的内容说话」
2. **只查不说 → 强制开口**：收尾时若这轮调过 web_search/read_url 却没发言，
   补的那一轮用 `tool_choice` **强制 send_message**（提示：「把结论说出来」），不再给她继续搜的机会
3. 提示词加「**优先用最新那条**」：搜索结果挑发布时间/版本号最新的看，别拿几个月前旧公告当现在的事

仿真结果（同一句 @ 提问）：

```
调用序列: web_search → read_url → （强制）send_message
她实际要发的内容: ['我翻了下官网公告，最近一版是「雪淞幽夢」，10月初那会儿上的，…',
                  '下一个版本的具体日期官方还没放，只有前瞻预告，得再等等']
```

## 边想边说（2026-10-08）

以前 `agent/loop.py` 是"`tools.spoke` 就 break"——她一发言这轮就结束，所以只能"想完了一次性说"，
多句话也只能靠 `|||` 塞进一次调用。现在改成**只有 `finish` 才收工**：

- `agent/loop.py`：收工条件从 `tools.spoke or tools.finished` 改成 `tools.finished`
  → 可以「先回一句 → 去查 → 回来再说」，中间的工具照跑
- `Tools.send_message`：新增每轮发言上限 `agent.max_sends_per_turn`（默认 3），超了回
  「这一轮你已经说了 N 条了…还有话就并进上一条，不然就调 finish 收工」
- 回执也带提示：「已发出 N 条（还想说/还要查就继续调工具；说完了就调 finish 收工）」
- **发送串行化**：新增 `_send_lock(gid)`，`_agent_send` / `_agent_send_sticker` 发送时按会话加锁
  → 多条消息按调用顺序到达（以前并发发会乱序，出现过两条顺序颠倒）
- `agent.max_rounds` 3→4：给"先回一句 + 查 + 回答案"留出轮数；**轮数上限就是这轮的成本上限**
- 提示词：`finish` 的描述改成"话说完/决定不发言时调它收工"；`_agent_tools` 里加了
  「可以边说边想：每次 send_message 都是一条立刻发出的消息…先发一句『我这去查』，查完再发结论」
- 回归：`tests/agent_loop_sim.py` 补了「边想边说（4 轮：说→查→说→finish）」和「每轮发言上限」两组用例

仿真实测（真模型 + 真搜索 + 桩接发送，同一句提问）：

```
她实际要发的内容: ['查了，官网那份公告写的是4月17号06:00停机维护更新「春晓时」',
                  '不过那是4月的公告，版本日历上1.5.3是9月1号更的，具体下一个得看最新的']
```

注意：允许边说边做以后，**一轮最多 4 次模型调用**（max_rounds），想省钱就把 `max_rounds` 调回 2~3。

### 边想边说 · 第二版：先打招呼 + 连发提醒 + 结论必达（2026-10-08）

第一版只做到"允许中途发言"，但实测她**先查完才一次性发**，不是"先说一句我这就去查"。
第二版把它变成结构性的：

1. **先打招呼（ack_first）**：判定"这问题必须查"（且被 @ / 被引用，`search.ack_only_directed`）时，
   主循环之前先跑一轮 **强制 `send_message`** 的招呼轮：「先说一句短的（≤15 字）告诉对方你去查，
   不要现在给结论」。日志：`边想边说：先去查的招呼 → 已发`
2. **连发提醒（软提醒，不再硬拦 3 条）**：`agent/loop.py` 每轮结束都会数她这一轮发了几条，
   ≥2 条就往下一轮塞一句「你已经连发 N 条了，没什么必要的就 finish 收工」。
   硬上限放宽到 `agent.max_sends_per_turn=6`（只在极端情况兜底）
3. **结论必达**：查过了但还没给结论（常见：只发了句"等下我翻翻"就把轮数用光）→
   强制补一轮 `send_message` 把结论说出来。日志：`边想边说：查过了但还没给结论（已发 6 字）→ 强制补一条结论`

仿真验证（真模型 + 真搜索 + 桩接发送 + 模拟 @ 她）：

```
'等下，我翻翻'
'查到了，官方那边写的是新版本「丹青渡」10月15日开，另有个淞幽梦的活动10月1日就起了'
'具体停机时间还没看到准的，以游戏里公告为准'
```

配置：`search.ack_first`（默认 true）/`search.ack_only_directed`（默认 true，避免群里太吵）/
`agent.max_sends_per_turn`（硬上限，默认 6）。
成本提醒：走"招呼 + 查 + 结论"最多 5 次模型调用（max_rounds=4 + 1 次结论补轮）。
