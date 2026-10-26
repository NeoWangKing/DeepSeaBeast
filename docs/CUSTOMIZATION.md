# 哪些东西是你自己的（用户私有）

> 一句话：**仓库里只有"通用代码 + 带默认值的模板"，你部署时那份东西一律叫 `*__custom__*`，
> 部署和更新永不覆盖它。**
> 判定规则参考 Nyxuri 的 `__custom__` 协议：名字里含 `__custom__` 就是用户的。

## 1. 文件归属表

| 位置 | 谁的 | 更新时 |
|---|---|---|
| `core/` `adapters/` `plugins/*/main.py` `docs/` | 项目 | 直接覆盖 |
| `deploy/core.__custom__.toml` | 项目（**模板**） | 覆盖 |
| `/opt/deepsea/core__custom__.toml` | **你** | **绝不动**，只提示新增键 |
| `data/plugins/personas/mapping__custom__.json` | **你** | **绝不动** |
| `data/plugins/personas/cards/**` | **你**（自己的卡） | **绝不动** |
| `data/`（记忆、最近对话、待办、贴图库） | **你** | **绝不动** |
| `.secrets/` | **你** | **绝不动** |
| `deploy/state/deployed.json` | 部署账本（自动维护） | 由 sync.sh 重写 |

`sync.sh` 里 `rsync --delete` 显式排除了这些路径——**这是硬规则，不靠账本判断**。

## 2. 你必须改的配置

| 键 | 作用 | 默认 |
|---|---|---|
| `owner_ids` | 你的账号（私聊、特权判断） | `[]` ← **必须填** |
| `[llm] api_key_file` | 模型密钥文件路径 | `.secrets/deepseek.key` |
| `[adapters.onebot] enabled` | 开 QQ 通道 | `true` |
| `[adapters.*] token_file` / `password_file` | 各通道口令文件 | 见模板注释 |

## 3. 按需改的配置

| 键 | 作用 | 默认 |
|---|---|---|
| `allowed_groups` | 哪些群归她管（空=全部） | `[]` |
| `speak_groups` | 哪些群不用 @ 也按概率接话 | `[]` |
| `only_when_addressed` | 哪些群只理被叫到的 | `[]` |
| `[gate] force_mode` | `off`（只认被叫）/`peak`/`offpeak` | `off` |
| `[gate] reply_probability` | 非被叫时的接话概率 | `0.2` |
| `[gate] dialogue_window_sec` | 连续对话窗口（0=关） | `0` |
| `[turn] max_rounds` / `max_tokens` | 回合预算 | `8` / `3000` |
| `[todos] max_open` | 每会话待办上限 | `12` |
| `[send] max_retries` | 发送失败重试次数 | `2` |

## 4. 不用改的（项目内部）

`[pacing]` 的节奏参数、`[memory] budget_tokens`、插件 `[[plugins]]` 的 timeout/priority、
工具的能力声明（`requires`）、提示词的拼装顺序——这些改了容易出问题，改之前先看 `docs/PRINCIPLES.md`。

## 5. 人格卡怎么放

| 放哪 | 用途 |
|---|---|
| 仓库 `plugins/personas/cards/` | 随项目发布的通用卡（模板卡、通用人格） |
| `data/plugins/personas/cards/` | **你自己的卡**（群卡、私人卡、外部导入的角色卡） |
| `~/Projects/persona_cards/` 之类外部目录 | 研究仓库，通过 `cards_dirs` 挂进来（可选） |

群号 → 卡片的对应关系写在 `data/plugins/personas/mapping__custom__.json`，
首次运行会从仓库的 `mapping.example.json` 复制一份，之后只改那份。

卡的规范（一张卡 = 一个目录，`export/system-prompt.txt` 优先当正文）见
`plugins/personas/README.md`；外部卡目录挂在
`data/plugins/personas/settings__custom__.json` 的 `cards_dirs` 里。

## 6. 想看模板变了什么

```bash
./deploy/sync.sh --check      # 只对比，不动服务器
```

会输出三段：

```
⚠️ 你手改过的代码文件（这次会被新版本覆盖，先备份）
＋ 模板新增的文件
？ 磁盘上有、账本里没有（不碰，来源不明）
```

外加配置差异（模板有、你没有的键）。
