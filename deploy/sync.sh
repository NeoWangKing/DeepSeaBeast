#!/usr/bin/env bash
# 同步代码到服务器并重启核心。**绝不覆盖服务器上的配置/密钥/数据**。
#
# 用法：./deploy/sync.sh [--no-restart]
set -euo pipefail

HOST="${DEEPSEA_HOST:-agent@117.72.111.241}"
KEY="${DEEPSEA_KEY:-$HOME/.sshhelper/agent_key}"
DEST="${DEEPSEA_DEST:-/opt/deepsea}"
RESTART=1
[ "${1:-}" = "--no-restart" ] && RESTART=0

cd "$(dirname "$0")/.."

echo "→ 同步代码到 $HOST:$DEST"
rsync -az \
  --exclude '.venv' --exclude '__pycache__' --exclude 'data' \
  --exclude '.git' --exclude '.secrets' --exclude 'core.toml' \
  -e "ssh -i $KEY -o StrictHostKeyChecking=no" \
  ./ "$HOST:/tmp/deepsea_src/"

ssh -i "$KEY" "$HOST" "sudo cp -a /tmp/deepsea_src/. $DEST/ && sudo chown -R astrbot:astrbot $DEST"

# 同步后自检：配置里的端口必须是 6199（SnowLuma 连这里）
PORT=$(ssh -i "$KEY" "$HOST" "grep -E '^port' $DEST/core.toml | head -1")
echo "→ 服务器配置：$PORT"

if [ "$RESTART" = "1" ]; then
  echo "→ 重启 deepsea-beast"
  ssh -i "$KEY" "$HOST" "sudo systemctl restart deepsea-beast && sleep 6 && sudo journalctl -u deepsea-beast --since '1 minute ago' --no-pager | grep -E '监听|连接已建立|Traceback|ERROR' | tail -4"
fi
