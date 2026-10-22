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

## 首次部署

```bash
# 1. 同步代码（本机执行）
rsync -az --exclude .venv --exclude __pycache__ --exclude data --exclude .git --exclude .secrets \
  -e "ssh -i ~/.sshhelper/agent_key" ./ agent@117.72.111.241:/tmp/deepsea_src/
ssh -i ~/.sshhelper/agent_key agent@117.72.111.241 \
  'sudo cp -a /tmp/deepsea_src/. /opt/deepsea/ && sudo chown -R astrbot:astrbot /opt/deepsea'

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
