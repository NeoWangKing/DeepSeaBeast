# 大肥鱼 · qq_peak_gate

QQ 群机器人「大肥鱼」的 AstrBot 插件（群聊门控 + 人格 + 长期记忆 + 海龟汤 + 表情包 + 本地资料库）。

## 目录
| 路径 | 作用 |
|---|---|
| `main.py` | 插件主入口：门控打分、人格注入、记忆注入、群白名单、拍一拍、表情包收发 |
| `scoring.py` | 零 token 的"这条值不值得回"打分器 |
| `turtle/` | 海龟汤：`puzzles.py` 题库 / `judge.py` 判题 / `session.py` 对局 / `gen.py` AI 出题 / `import_web.py` 网页题库导入 |
| `memory/` | 长期记忆：群印象、人物档案、每日批处理刷新 |
| `kb.py` | 本地小资料库（`data/kb/` 里的 md/txt 建索引，聊天时按相关度注入） |
| `stickers.py` | 表情包收藏夹（自动收图 + 识图打标 + 挑选发送，可同步到 QQ 账号表情） |
| `personas/` | 人格卡（`_template.txt` 是模板；具体群卡内容不入库） |
| `system_prompt*.txt` | 默认人格提示词（群聊 / 私聊 / 毒舌 / 工具档） |
| `gate_sim.py` | 离线测试台（57 项场景，不需要联网/QQ 即可跑） |
| `data/` | 运行数据（**内容不入库**）：记忆、对局、表情、资料库、群注册表 |

## 常用运维命令（服务器上）
```bash
qqbot-reload          # 热重载插件（不重启、不断连；有对局时会自动拒绝）
qqbot-restart         # 整机重启 AstrBot（会断连 ~15s，尽量少用）
qqbot-sticker ...     # 表情包：list/stats/add/del/tag/sync/faces/history/push
qqbot-kb ...          # 资料库：build/search/list/stats
python3 gate_sim.py   # 跑测试（全绿再部署）
```

## 约定
- 密钥放 `.secrets/`（已 ignore），配置里只写 `file:.secrets/xxx.key`。
- 改动流程：本地改 → `python3 gate_sim.py` 全绿 → scp 到服务器 → `qqbot-reload` → 看日志。
- 详细运维手册在 `/opt/astrbot/OPS.md`（§1–§60+）。
