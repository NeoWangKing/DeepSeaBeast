#!/usr/bin/env bash
# 同步代码到服务器并重启核心。
# 只同步"代码白名单"，绝不碰服务器上的 core.toml / .secrets / data（踩过两次坑）。
#
# 用法：./deploy/sync.sh [--no-restart]
set -euo pipefail

# 目标必须由调用者提供（仓库里不放任何服务器地址/密钥路径）
HOST="${DEEPSEA_HOST:?请先设置 DEEPSEA_HOST=user@主机}"
KEY="${DEEPSEA_KEY:?请先设置 DEEPSEA_KEY=/path/to/ssh_key}"
DEST="${DEEPSEA_DEST:-/opt/deepsea}"
RESTART=1
[ "${1:-}" = "--no-restart" ] && RESTART=0

cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 白名单：只拷这些（core.toml / .secrets / data 一律不进）
for p in core adapters plugins tests deploy docs Makefile README.md pyproject.toml requirements.txt core.example.toml; do
  [ -e "$p" ] && cp -a "$p" "$TMP"/
done
find "$TMP" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "→ 同步到 $HOST:$DEST"
RTMP="/tmp/deepsea_src_$$"
ssh -i "$KEY" "$HOST" "rm -rf $RTMP && mkdir -p $RTMP"      # 每次全新目录，杜绝旧文件混入
rsync -az -e "ssh -i $KEY -o StrictHostKeyChecking=no" "$TMP"/ "$HOST:$RTMP/"
ssh -i "$KEY" "$HOST" "sudo cp -a $RTMP/. $DEST/ && sudo chown -R astrbot:astrbot $DEST && rm -rf $RTMP"

PORT=$(ssh -i "$KEY" "$HOST" "grep -E '^port' $DEST/core.toml | head -1 || echo '（缺少 core.toml）'")
KEYPATH=$(ssh -i "$KEY" "$HOST" "grep -E '^api_key_file' $DEST/core.toml | head -1 || echo '（未配置 key）'")
PLUGINS=$(ssh -i "$KEY" "$HOST" "grep -E '^name = ' $DEST/core.toml | tr '\n' ' ' || echo '（没有插件）'")
echo "→ 服务器配置：$PORT / $KEYPATH"
echo "→ 已装插件：$PLUGINS"

if [ "$RESTART" = "1" ]; then
  ssh -i "$KEY" "$HOST" "sudo systemctl restart deepsea-beast && sleep 7 && sudo journalctl -u deepsea-beast --since '1 minute ago' --no-pager | grep -E '监听|连接已建立|ERROR|Traceback' | tail -4"
fi
