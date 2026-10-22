# 部署（服务器）

以 2026-10-22 的 M5 上线为准：**SnowLuma（QQ 协议端）保留，AstrBot 停用，核心自己跑。**

## 服务器布局

```
/opt/deepsea/                核心代码（git 里同步过来）
  .venv/                     python3.11 + websockets
  core.toml                  本机配置（不进 git；模板见 core.example.toml）
  .secrets/deepseek.key      LLM key（600，astrbot 可读）
  data/                      记忆 / recent / 缓存（不进 git）
```

## 配置文件

服务器配置模板在 `deploy/server.core.toml`（不含密钥明文）。**它不会被 sync.sh 覆盖**：

```bash
sudo -u astrbot cp deploy/server.core.toml /opt/deepsea/core.toml
```

关键项：
- `[adapters.onebot] enabled = true` —— 没有它核心会报"没有启用任何通道"直接退出
- `[adapters.cli] enabled = false` —— 服务器上开 CLI 会因 stdin EOF 立刻退出
- `api_key_file` 必须是服务器路径（`/opt/deepsea/.secrets/deepseek.key`）

## 首次部署

```bash
# 1. 同步代码（本机执行）
# 用脚本（自动排除 core.toml / .secrets / data，不会覆盖服务器配置）
./deploy/sync.sh
DEEPSEA_HOST=<user>@<服务器> DEEPSEA_KEY=<你的私钥> ./deploy/sync.sh

# 2. 依赖
sudo -u astrbot uv venv --python 3.11 /opt/deepsea/.venv
sudo -u astrbot uv pip install --python /opt/deepsea/.venv/bin/python websockets

# 3. 配置 + key
sudo -u astrbot cp /opt/deepsea/core.example.toml /opt/deepsea/core.toml   # 再按需改
sudo install -d -m700 -o astrbot -g astrbot /opt/deepsea/.secrets
echo '<deepseek key>' | sudo tee /opt/deepsea/.secrets/deepseek.key >/dev/null
sudo chown astrbot:astrbot /opt/deepsea/.secrets/deepseek.key && sudo chmod 600 /opt/deepsea/.secrets/deepseek.key

# 4. systemd
sudo cp deploy/deepsea-beast.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## 切换（QQ 一条连接只能给一个进程）

```bash
sudo systemctl stop astrbot            # 释放 127.0.0.1:6199
sudo systemctl disable astrbot         # 防止重启后两个抢端口（单元保留）
sudo systemctl enable --now deepsea-beast
# SnowLuma 会在 2 秒内自动重连到新的 6199
```

## 回滚（30 秒）

```bash
sudo systemctl stop deepsea-beast
sudo systemctl enable --now astrbot
```

## 上线前备份（做过一次，保存在 /opt/backups/）

```
astrbot-<时间戳>/
  plugin.tar.gz      旧插件全量（代码 + data + .secrets）
  cmd_config.json    AstrBot 主配置（含 provider key）
  *.unit.txt         systemd 单元快照
  enabled.txt/active.txt  切换前状态
```

## 自检

```bash
sudo -u astrbot /opt/deepsea/.venv/bin/python -m core --channel cli   # 本地聊两句
journalctl -u deepsea-beast -f                                        # 看线上日志
```

## 常犯的错（都踩过）

1. **把 core.toml 同步上去** → 服务器端口被改成开发机的 6200，SnowLuma 连 6199 连不上，
   表现是"她完全不说话"。`deploy/sync.sh` 已排除该文件。
2. **事件处理 await 在读循环里** → `send_group_msg` 等回包死锁，30 秒超时后模型重发，
   表现是"同一句话回两遍"。
3. **切换时两个服务抢 6199** → 一定要 `systemctl disable astrbot`，否则重启后互相踢。

## 用 telnet 和她聊天（连的是**运行中的核心**）

TCP 通道只绑 `127.0.0.1:4000`（不占公网端口），三种连法：

TCP 通道只监听 `<服务器>:127.0.0.1:4000`，**不是公网端口**，所以本机连不上，
需要一条 SSH 隧道：

```bash
# 终端 A：建立隧道（保持开着）
ssh -N -L 4000:127.0.0.1:4000 <user>@<服务器>

# 终端 B：连本机隧道口
telnet 127.0.0.1 4000      # 或 nc 127.0.0.1 4000
```

在服务器上直接聊：`ssh <user>@<服务器>` 之后 `nc 127.0.0.1 4000`。

> 想直接 `telnet <服务器IP> 4000`（不建隧道）需要把 `[adapters.tcp]` 的 `host` 改成
> 服务器网卡地址并在防火墙放行 —— 那是**新增一个公网端口**，默认不做；
> 若确实要开，务必配 `token = "..."`，否则任何人都能和她聊天并消耗你的额度。

- 纯文本行：打一行回车发送，她每说一句回一行（连发就是多行）
- `/quit` 或关窗口退出
- 走的是 `local` 作用域：和 CLI 同一份记忆，工具集只有文本（不会让她在终端里发表情包）
- 聊天内容会写进 `data/recent/local.json`，QQ 那边互不影响

## 通道一览

| 通道 | 绑定 | 用途 |
|---|---|---|
| `onebot` | `127.0.0.1:6199` | QQ（SnowLuma 反向 WS） |
| `tcp` | `127.0.0.1:4000` | telnet/nc 直接聊 |
| `socket` | `/tmp/deepsea.sock` | 本机脚本接入（`deploy/chat.py`） |
| `cli` | stdin | 本地开发（服务器上关闭） |
