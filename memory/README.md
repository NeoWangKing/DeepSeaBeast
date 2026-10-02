# memory/ —— 记忆子系统

**职责**：批处理生成「群印象」和「人物档案」；**不参与对话**。
对话侧（main.py）只 **读** 文件并注入 prompt，永远不在这里生成 —— 所以慢、贵、可失败的活都在本目录。

## 文件

| 文件 | 职责 |
|---|---|
| `llm.py` | **唯一的模型出口**。base_url / model / api_key 全来自 `config.json` 的 `memory.provider` |
| `store.py` | 产物读写（原子写）、素材收集、**证据校验**（结论必须能在原话里找到出处） |
| `prompts.py` | 提示词模板（结构化 JSON + 来源锚点要求） |
| `build_group_profile.py` | 生成「群印象」→ `data/memory/group_profile/<群号>.md` |
| `build_members.py` | 生成「人物档案」→ `data/memory/members/<群号>.json` |
| `refresh.py` | 总入口（守卫判断 → 按需生成），给 systemd timer 调 |

## 换模型（以后交给免费模型，只改这一处）

```json
"memory": { "provider": { "base_url": "https://open.bigmodel.cn/api/paas/v4",
                          "model": "glm-4-flash",
                          "api_key_source": "file:/root/glm.key",
                          "json_mode": false } }
```
`api_key_source` 支持：`astrbot`（复用 AstrBot 的 key）／`file:/路径`／`env:变量名`／`inline:密钥`。
免费模型若不支持 `response_format`，把 `json_mode` 设 false 即可（我们用 `parse_json` 容错解析）。

## 手动跑

```bash
cd /opt/astrbot/data/plugins/qq_peak_gate
python3 -m memory.refresh --force          # 强制刷新两个
python3 -m memory.build_group_profile 869622030
python3 -m memory.build_members 869622030
```

## 安全边界

- 只处理 `memory.groups` 里列出的群；不落盘的群（`no_log_groups`）天然没素材
- 任何一条结论都要通过**证据校验**，不然整份丢弃、保留旧版（防 AI 编造）
- 人物档案**禁止评价性内容**，只写话题/风格/口头禅
