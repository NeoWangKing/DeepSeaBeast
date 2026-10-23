#!/bin/bash
# DeepSeaBeast 数据每日备份：记忆空间、会话上下文、待办、贴图索引（不备 17 张图片本体以外的垃圾）
# 由 deepsea-backup.service / .timer 调用。
set -euo pipefail

DATA="${DEEPSEA_DATA:-/opt/deepsea/data}"
DEST="${DEEPSEA_BACKUP_DIR:-/opt/backups}"
KEEP="${DEEPSEA_BACKUP_KEEP:-7}"

mkdir -p "$DEST"
stamp=$(date +%Y%m%d-%H%M%S)
out="$DEST/deepsea-data-$stamp.tgz"

# 只备数据，不备代码（代码在 git 里）
tar czf "$out" -C "$(dirname "$DATA")" \
    --exclude='data/plugins/*/library_rejected' \
    --exclude='data/*.tmp' \
    "$(basename "$DATA")"

# 校验一下不是空包
size=$(stat -c %s "$out")
if [ "$size" -lt 1024 ]; then
    echo "[ERROR] 备份文件异常小（$size 字节）：$out" >&2
    exit 1
fi

# 只留最近 KEEP 份
ls -1t "$DEST"/deepsea-data-*.tgz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -f "$old"
done

echo "备份完成：$out（$((size / 1024)) KB，保留最近 $KEEP 份）"
