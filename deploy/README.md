# 部署说明（给别人的机器）

这套东西是 **AstrBot 的插件**，不是独立机器人。别人要跑起来需要：

## 前置
1. **AstrBot 4.x**（插件目录 `data/plugins/`）；
2. **一个 QQ 协议端**：推荐 SnowLuma + LinuxQQ（本项目就是在上面跑的），或任意 OneBot v11 实现；
3. **Python 3.12**：判题/识图/记忆都走 HTTP，只有图片去重和繁简转换需要 `Pillow`、`zhconv`（可选）；
4. **一把智谱 key**（`glm-4v-flash` 识图 + `glm-5.3-flashx` 批处理），放进 `.secrets/glm.key`；
5. **对话模型**：在 AstrBot 面板里配（本项目用的是 DeepSeek `deepseek-chat`）。

## 装
```bash
git clone <本仓库> ~/qq_peak_gate
cp -r ~/qq_peak_gate /opt/astrbot/data/plugins/qq_peak_gate   # 或直接放进去
cd /opt/astrbot/data/plugins/qq_peak_gate
cp deploy/config.example.json config.json     # 然后填群号/机器人QQ
echo "<你的智谱key>" > .secrets/glm.key && chmod 600 .secrets/glm.key
sudo bash deploy/install.sh                   # 装 CLI 工具 + 定时任务
sudo systemctl restart astrbot
journalctl -u astrbot -n 50 | grep qq_peak_gate    # 看到 Loading plugin 就成了
```
> 工具默认假设插件在 `/opt/astrbot/data/plugins/qq_peak_gate`；换路径就设环境变量
> `QQBOT_PLUGIN_DIR`（Python 解释器路径用 `QQBOT_PY`），或直接改 `deploy/bin/*` 里的默认值。

## 定时任务（install.sh 会自动启用）
| 任务 | 作用 |
|---|---|
| `qqbot-memory-refresh` | 每小时：群印象 / 人物档案批处理刷新 |
| `qqbot-memes-refresh` | 每天：从群聊记录提炼黑话（memes.md） |
| `qqbot-holiday-refresh` | 每天：刷新节假日表（决定高峰期） |
| `qqbot-turtle-gen` | 每天 04:20：给海龟汤题库补题（到目标数就跳过） |
| `qqbot-qq-reconnect` | 每 3 分钟：QQ 桥接断了就自动重连 |
| `qqbot-backup` | 每天 03:40：整站备份（保留 7 天） |

## 数据与隐私
- **不入库**：`.secrets/`、`data/memory/`、`data/turtle/`、`data/stickers/`、`data/chatlog/`、
  `data/kb/` 内容、`prompts/personas/*.txt`（群人格卡）——都在 `.gitignore` 里；
- 群里聊过的内容只落在**本机**，不会因为 clone/推送仓库而外泄；
- 想清空：删对应 `data/` 子目录即可。

## 常见问题
- **`qqbot-reload` 被拒绝**：说明有海龟汤对局在进行，等结束或 `qqbot-reload --force`；
- **重启后她不说话**：多半是协议端没挂上 QQ，跑 `qqbot-qq-reconnect`（或看它的日志）；
- **表情包发不出去**：图片走 QQ 直链常被丢，工具会先落盘成本地文件再发；发不出看 `journalctl -u astrbot | grep 表情包`；
- **判题/识图报错**：先 `qqbot-doctor`，再看 `.secrets/glm.key` 是否可读、额度是否用完。
