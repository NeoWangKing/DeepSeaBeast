# personas（人格卡）

按"这是哪儿"给不同地方发不同的人格卡。**它不管平台细节**（哪些是平台习惯由 adapter 的
`profile()` 说），只管"她在这个地方是谁、这里有什么规矩"。

## 选择顺序

```
by_group[群号]  >  by_private[QQ号]  >  by_channel[通道名]  >  自动建档
```

## 卡片两级

| 位置 | 用途 |
|---|---|
| `cards/`（仓库，只读） | 内置种子卡，跟着代码走 |
| `$DEEPSEA_PLUGIN_DATA/cards/`（运行时，可写） | 自动建档、手工补充 |

查找时**先运行时后内置**，所以手工改运行时那张就能覆盖内置。

## 自动建档

第一次见到某个群/某个人/某个通道，如果没有对应卡，就按 `mapping.json` 的 `filing` 模板建一份：

```json
"filing": {
  "group":   "groups/{id}.md",
  "private": "privates/{id}.md",
  "local":   "channels/{channel}.md"
}
```

模板是 `cards/_template.txt`，可用变量：`{label}` `{name}` `{id}` `{channel}` `{date}`。

群名/昵称由 adapter 提供（`scope_info`），拿不到就只写 id。

## 加一个新群

- 想给它专属卡：在 `mapping.json` 的 `by_group` 里加一行 → 或在运行时目录建 `groups/<群号>.md`
- 什么都不做也行：会自动建一张空卡，先按"还不了解这里"的方式说话
