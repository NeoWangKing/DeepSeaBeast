# personas（人格卡）

按会话挑一张人格卡，把它的正文拼进提示词。**卡是数据，插件只负责选和读。**

## 一张卡 = 一个目录（推荐）

```
cards/<代号>/
  export/system-prompt.txt   ← 正文（编译好的扮演提示词）**优先用它**
  persona.md                 ← 没有 export 时的正文（人读的完整版）
  prompt.txt                 ← 最老的格式，兼容
  voice.md                   ← 说话风格（会追加）
  rules.md                   ← 硬约束（会追加）
  meme-mode.md               ← 梗模式（settings 开了才追加）
  memes.md                   ← 梗库（同上）
  examples.md                ← 示例对话（settings 开了才追加）
  profile.md / sources.md / cross-references.md / README.md
                             ← **考据资料，不进提示词**
  references/ assets/ export/*.json
                             ← 原始素材 / 前端导出，不读
```

**加载规则**（固定，不靠猜）：

1. 正文按顺序取第一个存在的：`export/system-prompt.txt` → `persona.md` → `prompt.txt`
2. 追加只有**白名单**里的：`voice.md`、`rules.md`（总是）；`meme-mode.md`、`memes.md`（`meme_mode` 开）；`examples.md`、`dialogue-examples.md`（`include_examples` 开）
3. 拼完超过 `max_chars` 就按顺序截断，并在日志里说明
4. 日志会写明：「正文用 X，追加 Y，共 N 字」——**一眼能看出这次用了什么**

为什么只追加白名单：`persona_cards/Skadi/profile.md` 有 17KB 考据、`cross-references.md` 5KB——
全塞进提示词只会烧 token，对"像不像她"没有帮助。

## 用户设置（私有）

`$DEEPSEA_PLUGIN_DATA/settings__custom__.json`（首次自动生成，更新不覆盖）：

```json
{
  "cards_dirs": [],          // 额外卡目录，比如 ["~/Projects/persona_cards"]
  "meme_mode": false,        // 要不要追加梗模式
  "include_examples": false, // 要不要追加示例对话
  "max_chars": 12000         // 一张卡拼完的上限
}
```

## 命名规矩：用代号，不用编号

```
cards/Skadi/               ✅ 角色卡用角色代号
cards/kaltsit/             ✅
cards/groups/debug/        ✅ 群卡用"用途代号"，群号只写在 mapping 里
cards/groups/miss-rai.txt  ✅
cards/1109525246/          ❌ 用群号当目录名（分不清谁是谁）
```

`groups/` 放"某个群的专属卡"，`channels/` 是自动建档的通道卡。
群号 ↔ 卡片的对应关系**只存在于 mapping 文件里**，目录名永远是人话。

## 卡片仓库（按优先级）

1. `$DEEPSEA_PLUGIN_DATA/cards/` —— **只放跟这台部署绑定的**（自动建档的 `channels/`、临时实验卡）
2. `plugins/personas/cards/` —— **人格卡本体都在这**：角色卡（`Skadi/`、`Kal'tsit/`、`Amiya/`）、
   群卡（`groups/debug/`）、早期单文件种子卡。**你的作品属于内容资产，进仓库、能查看、能改、能 diff**
3. `settings.cards_dirs` 里列的外部目录 —— 研究仓库（`~/Projects/persona_cards`），
   放原始抓取资料 `references/` 那种大文件

> 判据：**卡的内容**进仓库（可版本化、可 review）；
> **群号 ↔ 卡片的对应关系**才留在 `data/`（那是部署信息）。

## 群号 → 卡片 的对应关系

`$DEEPSEA_PLUGIN_DATA/mapping__custom__.json`（首次从 `mapping.example.json` 复制）：

```json
{
  "by_group":   { "100000001": "Skadi" },
  "by_private": { "200000001": "system_prompt_private.txt" },
  "by_channel": { "cli": "terminal.txt", "tcp": "terminal.txt" }
}
```

优先级：`by_group` > `by_private` > `by_channel` > 自动建档。

## 体检

```bash
.venv/bin/python plugins/personas/check_cards.py
.venv/bin/python plugins/personas/check_cards.py --dir ~/Projects/persona_cards
```

输出每张卡"用哪个文件、多少字、有没有问题"，容器目录会往下递归看。
