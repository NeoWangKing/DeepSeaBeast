# 待办

只记"已知但还没做"的事。做完了就删掉这一行，别留成历史日志。

## 架构 / 重构

- **合并 `adapters/tcp` 和 `adapters/socket` → `adapters/terminal`**
  两者除了传输方式（TCP 端口 / unix socket）逻辑几乎完全相同：连接→scope 映射、
  `profile()`、`scope_info()`、`idle()` 握手各写了一遍，多连接那个 bug 也就各犯了一次。
  合并后只有一份连接管理。**时机：定多连接方案时一起做**，别现在单独搬。

- **多连接怎么隔离（未定方案）**
  现状：`ChatRef.local().key` 固定为 `cli`，`_writers[key]` 单槽 → 多人同时 telnet 会串台、
  上下文互相污染。候选：
  - A 每连接一个 scope（简单，断开失忆）
  - B 先 `login` 认身份，`scope=身份` + 另外用 `route` 指定回哪条连接（记忆连续，需要 `OutboundMessage` 加不透明 route 字段）
  - C 把多用户终端外包给 breamer，核心只暴露 socket 协议（核心最干净）

- **`OutboundMessage` 是否需要 `route` 字段**
  取决于上面选 A/B/C。一旦加了，所有 adapter 都要考虑，别随手加。

## 门控（决定要不要回话）

- **现在只认 @，是临时收紧，不是设计**
  `stage_decide` 里 `force_mode="off"` + `at_only_groups` 直接把"不该回"判了，
  模型连"要不要接话"都没被问过。上线初期为了别在群里乱说话才这么设。
- **目标结构**：
  1. 规则只做**便宜的过滤**（白名单 / 激活 / 频率 / 连发合并 / 她正在说话）
  2. **"接不接"交给模型**（`decide` 工具）：看是否被叫、是否在问她、话题有没有话说、
     群里什么气氛、最近她说过没
  3. **成本护栏**：被 @/提到名字/疑问句 → 必问模型；普通闲聊按 `chime_probability`
     （建议 10~20%）抽样后才问；群里消息过密时只在她被叫时判断
- **按群配置**：`reply_mode = auto | at_only | silent`，可每群覆盖
- 相关：`typing.py` 的"话没说完"判断、`wait` 后的 5 秒重判，都属于这一层

## 微信通道（长期想要，现在不做）

**结论（2026-10-23）**：长期要做，但现在没有能常开的 Windows 机器，先挂着。

难点：个人微信没有像 SnowLuma 之于 QQ 那样的现成网关。
微信 4.x 客户端只有 Windows/macOS 版，主流接入方式是
`version.dll` 代理注入（参考 [WeChatAgent](https://github.com/sourcepro5/WeChatAgent)
用的 [aixed/WeChat-Hook](https://github.com/aixed/WeChat-Hook)）——
必须在**真机桌面会话**里跑，Linux 服务器上没法起。

### 三条候选路径

| 路径 | 前提 | 评价 |
|---|---|---|
| 二手迷你主机常开 Windows（N100/N5105，8~16G，6~10W，¥400~800） | 家里有网有电 | **最现实**，一次性成本，长期电费极低 |
| 云上 Windows VM | 另买 Windows 授权；2C7G 的服务器要再升级 | 每月 ¥50~150+，还和 QQ 抢 CPU |
| 等微信 Linux 版出成熟 Hook | 无 | 目前没有，不可控 |
| 企业微信 / 公众号 | 官方 API | **不是个人微信群**，不算达标 |
| 付费协议库（padlocal 等） | 无硬件 | 封号风险高，不建议 |

### 真到那天要做的事（架构已经留好口子）

1. Windows 侧：微信 + Hook（本地 HTTP）→ 一个**小代理**把消息包成 WS 推给我们
2. 服务器侧：`adapters/wechat/channel.py`，实现五件事
   `describe / scope_info / profile / start / send`
3. `scope_info()` 给原始 key（`wx:<wxid>`），核心自己哈希成引用——**核心不用改**，
   人格、记忆、待办、工具全部原样复用

借鉴点（来自 WeChatAgent）：发送后要**核对数据库回执**再算成功；未确认的投递要持久化，
重启后先核对再决定是否重发。

## 记忆

- **memory 插件与 core/memory.py 的旧印象并行**：插件已有自己的空间（`spaces/`），
  但核心仍在读 `impression.md` / `members.json`。以后要么让插件写这些文件，
  要么让核心改读插件空间——现在两套并存，别再加第三套。
- ~~**global 空间的可见性**~~ ✅ 2026-10-24：已改成**硬约束**——写入 global 必须带
  `"public": true`，否则程序直接丢弃（`rejected_global`）；读取时 global 只注入 public 条目。
  原来只有提示词约束，
  但这是软约束。要么加标签（`visibility: private`）在注入时按 scope 过滤，
  要么让 global 只由人工维护。
- **self 层分公开/私密**（阻塞上面那条）
  `self/owner_public.md`（群里可用）与 `self/owner_private.md`（仅 CLI/私聊），
  否则私聊内容会漏到群里。
- **黑话 / 表达库注入策略**：文件已迁移，目前不注入；倾向"命中才给"。
- **notes.jsonl（理解笔记）**：目前不注入，等检索工具再说。
- **作用域上下文重置**：需要 `--reset-scope <key>`，上下文被错误回答污染时能一键清掉
  （踩过：她答错"能发文件"后，错误被写进 recent，后面几轮一直用它）。

## 可靠性

- ~~**发送回执核对**~~ ✅ 2026-10-24：发完调 `get_msg` 回读，查不到就判失败（`retcode=0` 只代表
  "接口收下了"）。对方不支持 `get_msg` 时自动关闭核对并警告一次，不误报。
  文件上传（`upload_group_file`）没有 message_id，暂无法核对。
- **统一的发送结果三态**：`SendResult.confirmed` 已加（True=确认到 / False=没法确认 / ok=False=明确失败），
  核心已记录"未确认"的发送；**重试策略还没做**（只重试 ok=False，不碰 confirmed=False）。

## 能力

- **表情包（贴图 / 图片表情）**：QQ 的"狗头"这类不属于 281 个原生 face，
  要走 SnowLuma 的 `send_custom_face`（见 `docs/SNOWLUMA_API.md`）；
  旧实现有 ~101MB 表情包数据可迁。
- **看图**：收到图片时用 `get_image` 取文件再交给视觉模型；现在图片只是 `<图片>` 占位。
- **联网搜索**：旧实现有，尚未迁移。
- **语音**：ASR/TTS 都未接（旧 `qqbot-asr` 已停）。
- **文件读写**：`send_file` 已有，但没有"生成文件"的能力（旧 `write_file` 未迁）。

## 运维 / 质量

- **可观测性最小版**：每天 token 花费、缓存命中率、工具失败率的汇总；健康检查端点。
- **数据备份**：`data/` 每日备份（开始写记忆之后必须有）。
- **发布回滚**：`deploy` 打 tag → 健康检查 → 失败自动回滚。
- **行为回归测试**：现有 16 个自测都是"机制正确"，缺"还像不像大肥鱼"的守门测试。
- **放开到其它群**：现在只开调试群（`allowed_groups`），推广前要评估。
