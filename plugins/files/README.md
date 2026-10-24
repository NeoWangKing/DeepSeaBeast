# files（文件读写）

让她**能写出东西来**，并且直接发出去。以前她只能收文件，不能产出。

## 四个工具

| 工具 | 类型 | 作用 |
|---|---|---|
| `write_file(name, text, send=true)` | action | 写成文件并发出去（一步到位） |
| `read_file(name)` | query | 读回自己写过的文本 |
| `list_files()` | query | 看沙箱里有什么 |
| `delete_file(name)` | control | 删掉 |

`write_file` 是唯一要求 `file` 能力的工具——终端/聊天室没有文件能力，那里它自动消失。

## 沙箱

所有文件都在 `$DEEPSEA_PLUGIN_DATA/files/` 下：

- 只接受简单文件名（`笔记.md`、`report.csv`），**目录部分会被剥掉**
- `..`、绝对路径、非法字符一律拒绝
- 写入上限 512KB，读回上限 200KB
- 落盘后再做一次 `realpath` 校验，确保没跑出沙箱

## 发送怎么走

插件只返回**意图**，核心负责送到通道：

```python
{"send": [{"kind": "file", "path": "/…/files/笔记.md", "name": "笔记.md"}]}
```

核心再判断这个通道有没有 `file` 能力（QQ 有，终端没有），然后由通道把文件发出去。

## 一个已验证的前提

QQ 这边文件**必须用 base64 上传**：SnowLuma 以另一个用户跑，读不到我们的目录
（实测 `file://` 和绝对路径都是 `EACCES`，`base64://` 才 retcode=0）。
单文件上限 8MB。
