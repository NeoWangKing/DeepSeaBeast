#!/usr/bin/env bash
# 同步代码到服务器并重启核心。
#
# 三条铁律（踩过两次坑之后定的）：
#   1. 只同步"代码白名单"，服务器上的用户私有内容一律不碰：
#      __custom__*、data/、.secrets/
#   2. 用户配置（core__custom__.toml / mapping__custom__.json）**只生成一次**，
#      之后部署/更新永不覆盖，只提示"模板新增了哪些键"
#   3. 每次部署记账（deploy/state/deployed.json），并报告你手改过的代码文件
#
# 用法：
#   ./deploy/sync.sh                 # 同步 + 重启
#   ./deploy/sync.sh --no-restart    # 只同步
#   ./deploy/sync.sh --check         # 只看差异，不动服务器
set -euo pipefail

HOST="${DEEPSEA_HOST:?请先设置 DEEPSEA_HOST=user@主机}"
KEY="${DEEPSEA_KEY:?请先设置 DEEPSEA_KEY=/path/to/ssh_key}"
DEST="${DEEPSEA_DEST:-/opt/deepsea}"
RUN_USER="${DEEPSEA_USER:-deepsea}"
RESTART=1
CHECK=0
for arg in "$@"; do
  case "$arg" in
    --no-restart) RESTART=0 ;;
    --check) CHECK=1 ;;
  esac
done

cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 白名单：只拷这些（core__custom__* / .secrets / data 一律不进）
for p in core adapters console plugins tests deploy docs Makefile README.md pyproject.toml requirements.txt core.example.toml; do
  [ -e "$p" ] && cp -a "$p" "$TMP"/
done
find "$TMP" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
# 仓库里的 __custom__ 模板不随代码铺过去（它是模板，不是用户那份）
find "$TMP" -name '*__custom__*' -not -path "$TMP/deploy/*" -delete 2>/dev/null || true
find "$TMP" -type d -name '*__custom__*' -not -path "$TMP/deploy/*" -prune -exec rm -rf {} + 2>/dev/null || true

RTMP="/tmp/deepsea_src_$$"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no $HOST"

echo "→ 同步到 $HOST:$DEST"
$SSH "rm -rf $RTMP && mkdir -p $RTMP"
rsync -az -e "ssh -i $KEY -o StrictHostKeyChecking=no" "$TMP"/ "$HOST:$RTMP/"

echo
echo "=== 部署计划（账本对比）==="
$SSH "sudo python3 $RTMP/deploy/tools/ledger.py plan --payload $RTMP --dest $DEST" || true
if [ "$CHECK" = "1" ]; then
  echo
  echo "（--check：没有改动服务器）"
  exit 0
fi

# 铺代码：排除用户私有内容（双保险，账本之外再挡一次）
# 不用 rsync --delete：那会把用户在部署目录里自己放的文件也铲掉。
# 旧模板文件由下面的 ledger prune 按"账本登记过的路径"精确清理。
$SSH "sudo rsync -a \
        --exclude '__custom__*' --exclude '*__custom__*' \
        --exclude 'data/' --exclude '.secrets/' --exclude '.venv/' \
        --exclude 'deploy/state/' --exclude '__pycache__/' --exclude '*.pyc' \
        --exclude 'core.toml' \
        $RTMP/ $DEST/ && sudo chown -R $RUN_USER:$RUN_USER $DEST"
$SSH "sudo python3 $RTMP/deploy/tools/ledger.py prune --payload $RTMP --dest $DEST"

# 用户配置：只生成一次，之后永不覆盖
$SSH "sudo bash -s" <<REMOTE
set -e
DEST="$DEST"; USER_="$RUN_USER"
# 旧名字迁移：core.toml → core__custom__.toml
if [ -f "\$DEST/core.toml" ] && [ ! -f "\$DEST/core__custom__.toml" ]; then
  mv "\$DEST/core.toml" "\$DEST/core__custom__.toml"
  echo "→ 已把旧的 core.toml 改名为 core__custom__.toml（内容没动）"
fi
if [ ! -f "\$DEST/core__custom__.toml" ]; then
  cp "\$DEST/deploy/core.__custom__.toml" "\$DEST/core__custom__.toml"
  chown "\$USER_:\$USER_" "\$DEST/core__custom__.toml"
  echo "→ 首次部署：生成了 core__custom__.toml（照着注释改它，别再改仓库模板）"
fi
REMOTE

$SSH "sudo python3 $DEST/deploy/tools/ledger.py record --payload $RTMP --dest $DEST"
$SSH "sudo python3 $DEST/deploy/tools/ledger.py check-custom \
        --custom $DEST/core__custom__.toml \
        --template $RTMP/deploy/core.__custom__.toml" || true

PLUGINS=$($SSH "grep -E '^name = ' $DEST/core__custom__.toml | tr '\n' ' ' || echo '（没有插件）'")
echo "→ 已装插件：$PLUGINS"

if [ "$RESTART" = "1" ]; then
  $SSH "sudo systemctl restart deepsea-beast && sleep 7 && sudo journalctl -u deepsea-beast --since '1 minute ago' --no-pager | grep -E '监听|连接已建立|ERROR|Traceback' | tail -4"
fi
