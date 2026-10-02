# DeepSeaBeast (DSB) · 大肥鱼

> 仓库名 `DeepSeaBeast`，缩写 **DSB**；「大肥鱼」是这台机器人在群里的昵称。

AstrBot 群聊机器人插件：**群聊门控 + 人格卡 + 长期记忆 + 本地资料库 + 海龟汤 + 表情包**。
（"大肥鱼"只是这台机器人的名字，改成你自己的即可。）

## 它做什么

- **门控**：不是每句话都回。零 token 打分器判断"这条值不值得回"，再叠冷却、每小时额度、群白名单；
- **人格**：按群挂不同人格卡（`prompts/`），支持自动给新群建档；
- **记忆**：群印象 + 人物档案定时批处理，回话时按相关度注入（隐私群可完全关闭）；
- **资料库**：`data/kb/` 里的 md/txt 建索引，聊天时按相关度把相关段落注入提示词；
- **海龟汤**：题库 + 判题（独立模型）+ 难度 + 要点进度 + 揭晓后追问 + AI 现编新题；
- **表情包**：自动收录群友发的梗图（识图打标、去重），在合适的时候挑一张发；可同步进 QQ 表情面板。

## 目录结构

```
main.py                    插件入口：门控 / 人格 / 记忆 / 资料库 / 表情包 / 玩法调度
scoring.py  kb.py  stickers.py   三个核心模块：回复打分 / 本地资料库 / 表情包收藏
prompts/                   人格提示词（personas/ 是群人格卡，内容不入库）
games/                     玩法插件目录：games/turtle_soup/ = 海龟汤
memory/                    长期记忆（群印象 / 人物档案 / 定时刷新）
tools/  tests/             运维脚本 / 离线测试台（gate_sim.py）
deploy/                    部署层：CLI 工具、systemd 单元、install.sh、示例配置
docs/OPS.md                运维手册（数据流 / 命令 / 排障 / 成本 / 隐私）
config.json                插件配置（群号、白名单、开关；不含密钥）
data/                      运行数据：记忆 / 对局 / 表情 / 资料库 / 日志（内容全部不入库）
```

每个文件/目录的详细说明见 [docs/OPS.md](docs/OPS.md)。

## 快速开始

```bash
# 1) 放到 AstrBot 插件目录
cp -r qq_peak_gate /opt/astrbot/data/plugins/
# 2) 依赖（装进 AstrBot 的 Python 环境）
/opt/astrbot/.local/share/uv/tools/astrbot/bin/pip install -r requirements.txt
# 3) 配置：填群号、机器人 QQ、人格映射
cp deploy/config.example.json config.json && vi config.json
# 4) 密钥（判题/识图/记忆用）
mkdir -p .secrets && echo "<智谱key>" > .secrets/glm.key && chmod 600 .secrets/glm.key
# 5) 装 CLI 工具 + 定时任务
sudo bash deploy/install.sh
sudo systemctl restart astrbot
```

详细部署（前置、定时任务、隐私、常见问题）见 **[deploy/README.md](deploy/README.md)**；日常运维见 **[docs/OPS.md](docs/OPS.md)**。

## 开发 / 改动流程

```bash
python3 tests/gate_sim.py     # 必须全绿
git add -A && git commit -m "…"
qqbot-reload                  # 热重载：不断连、不丢消息（有对局时会拒绝，--force 强开）
journalctl -u astrbot -n 50 | grep qq_peak_gate
```

## 隐私说明

`.secrets/`、`data/`（记忆、对局、表情、日志、资料库内容）、`prompts/personas/*.txt` **都不入库**，
聊天内容只留在部署机器上。对外分享前建议 `git ls-files` 自查一遍。
