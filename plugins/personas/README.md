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

## 卡片仓库（按优先级）

1. `$DEEPSEA_PLUGIN_DATA/cards/` —— 你自己的卡（群卡、私人卡、导入的角色卡）
2. `plugins/personas/cards/` —— 仓库里随包发的通用卡
3. `settings.cards_dirs` 里列的外部目录 —— 比如研究仓库 `~/Projects/persona_cards`

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
