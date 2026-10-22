#!/usr/bin/env bash
# 一条命令进聊天：走 SSH 到服务器的回环 nc（不占公网端口）
# 用法：./deploy/telnet.sh
set -euo pipefail
HOST="${DEEPSEA_HOST:-agent@117.72.111.241}"
KEY="${DEEPSEA_KEY:-$HOME/.sshhelper/agent_key}"
PORT="${DEEPSEA_TCP_PORT:-4000}"
exec ssh -t -i "$KEY" "$HOST" "nc 127.0.0.1 $PORT"
