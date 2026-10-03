#!/bin/bash
# 安装大肥鱼插件的外围部分：命令行工具 + systemd 定时任务
# 用法：sudo bash deploy/install.sh
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
[ "$(id -u)" = "0" ] || { echo "请用 root 运行：sudo bash deploy/install.sh"; exit 1; }
echo "插件目录：$REPO"
install -m 755 "$HERE"/bin/qqbot-* /usr/local/bin/
install -m 644 "$HERE"/systemd/*.service "$HERE"/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
for t in qqbot-sticker-mirror qqbot-memory-refresh qqbot-memes-refresh qqbot-holiday-refresh qqbot-turtle-gen qqbot-qq-reconnect qqbot-backup; do
  systemctl enable --now "$t.timer" >/dev/null 2>&1 || echo "  （跳过 $t.timer）"
done
mkdir -p "$REPO/.secrets" && chmod 700 "$REPO/.secrets"
cat <<TIP

装好了，还差三步：
  1) 把智谱 key 放进 .secrets/glm.key 并 chmod 600（判题/记忆/识图都用它）
  2) cp deploy/config.example.json config.json，填 allowed_groups / allowed_self_id / prompt_by_group
  3) systemctl restart astrbot，然后看日志：journalctl -u astrbot -n 50 | grep qq_peak_gate

自检：qqbot-doctor ；热重载：qqbot-reload ；状态：qqbot-status
TIP
