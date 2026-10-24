# jargon（黑话 / 表达库）

让她**听得懂这个群自己的说法**。两样东西：

| | 例子 | 来源 |
|---|---|---|
| **术语** `terms` | 「玉湖」= 某个人的简称 | 有人解释时 `learn_term` 记下 |
| **句式** `patterns` | 「已完成今日{名词}大学习」= 打卡腔玩梗 | 从旧实现迁移的 20 条 |

## 核心原则：命中才给

只有**当前这条消息里真的出现了**，才把解释塞进上下文；不命中就一个字都不给。
否则每轮都塞一堆"这个群可能这样说话"，既浪费 token 又干扰判断。

句式里的 `{名词}` 是占位符，匹配时当"任意几个字"处理：

```
已完成今日{名词}大学习   ←→  已完成今日325大学习   ✅
已完成今日{名词}大学习   ←→  今天吃了火锅        ❌
```

一次最多给 3 条解释（`MAX_LINES`），避免刷屏式背景信息。

## 三个工具

| 工具 | 类型 | 作用 |
|---|---|---|
| `learn_term(term, meaning)` | control | 有人解释了某个说法 → 记下来 |
| `list_terms()` | query | 看这个群记过哪些说法 |
| `forget_term(term)` | control | 记错/不用了 → 删掉 |

## 数据

```
$DEEPSEA_PLUGIN_DATA/<会话引用>.json
{"terms": [{term, meaning, kind, created}],
 "patterns": [{surface, note, kind, evidence, created}]}
```

按会话隔离（用的是核心给的不透明引用，插件不知道是哪个群）。

## 迁移

```bash
python3 deploy/migrations/006_import_jargon.py --dry   # 先看
python3 deploy/migrations/006_import_jargon.py
```

只迁**有解释的**：旧数据的 16 条术语全是 `pending`（没有释义），迁过去只会变成噪音，跳过；
20 条句式都带 `note`，全部迁入。

## 以后可以加

- **自动学习**：像 memory 插件那样攒素材、定期让模型挑出"这个群特有的说法"
  （现在只支持人教）
- 命中统计：哪些说法最常出现，值得优先记住
