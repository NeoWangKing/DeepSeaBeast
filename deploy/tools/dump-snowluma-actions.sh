#!/usr/bin/env bash
# 从服务器上 SnowLuma 的打包文件里抠出它支持的所有动作名（用于更新 docs/SNOWLUMA_API.md）
#
# 用法：./deploy/tools/dump-snowluma-actions.sh            # 打到标准输出
#       ./deploy/tools/dump-snowluma-actions.sh > /tmp/a.txt
#
# 需要先设置 DEEPSEA_HOST / DEEPSEA_KEY（同 deploy/sync.sh）
set -euo pipefail
HOST="${DEEPSEA_HOST:?请先设置 DEEPSEA_HOST=user@主机}"
KEY="${DEEPSEA_KEY:?请先设置 DEEPSEA_KEY=/path/to/ssh_key}"
DIR="${SNOWLUMA_DIR:-/opt/snowluma}"

ssh -i "$KEY" "$HOST" "sudo -n python3 - <<'PY'
import re, glob
names=set()
for f in glob.glob('$DIR/*.js')+glob.glob('$DIR/*.mjs'):
    s=open(f,encoding='utf-8',errors='ignore').read()
    for m in re.finditer(r'[\"\x27\`]([a-z][a-z0-9]*(?:_[a-z0-9]+){1,4})[\"\x27\`]', s):
        names.add(m.group(1))
verbs=('get_','set_','send_','delete_','upload_','download_','can_','mark_','_get_','_send_','_set_','create_','dismiss_','move_','rename_','kick_')
print('\n'.join(sorted(n for n in names if any(n.startswith(v) for v in verbs) and len(n)>6)))
PY"
