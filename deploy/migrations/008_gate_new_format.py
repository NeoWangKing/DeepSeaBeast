#!/usr/bin/env python3
"""把 [gate] 段从旧格式迁到新格式（agent 内部开发阶段用的一次性脚本）。

旧键：force_mode / speak_groups / at_only_groups / dialogue_window_sec(旧注释版)
新键：default_preset + [gate.chats."<kind>:<id>"] + credit_* 等

只在文本层面替换 [gate] 这一整段，文件其余内容（注释、其他段）原样保留。
用法（在服务器上）：
  sudo -u deepsea .venv/bin/python deploy/migrations/008_gate_new_format.py --dry
  sudo -u deepsea .venv/bin/python deploy/migrations/008_gate_new_format.py
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import time
import tomllib

PATH = "/opt/deepsea/core__custom__.toml"

HEADER = '''[gate]
# 三档位：strict（只认被叫到）/ balanced（被叫必问，否则打分）/ loose（每条都问模型）
# 详见 docs/GATE.md
default_preset = "strict"
allowed_groups = %(allowed)s        # 留空 = 所有群都管
credit_max = 3                      # 发言额度上限（像"还有几次开口机会"）
credit_refill_min = 15              # 每 15 分钟回 1 点
score_threshold = 2.0               # 场面分超过它才值得问模型
max_decide_per_hour = 30            # 每小时最多问几次模型（成本硬上限）
dialogue_window_sec = 120           # 连续对话窗口：这么久内同一个人继续说就不重新打分
dialogue_max_turns = 6              # 一次对话最多几个来回
dialogue_min_interval_sec = 2.5     # 对话中的最小间隔
dialogue_refill_boost = 2.0         # 对话中额度回充加速
private_owner_only = false
merge_window_sec = 10
typing_wait_sec = 8
typing_max_wait_sec = 40
burst_suppress_sec = 30
min_interval_sec = 8
max_auto_per_hour = 30

# ── 按会话覆盖（键 = "通道种类:会话id"）──
%(chats)s'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    path = os.path.expanduser(PATH)
    text = open(path, encoding="utf-8").read()
    data = tomllib.loads(text)
    gate = data.get("gate") or {}

    if "default_preset" in gate:
        print("已经是新格式了，不用迁")
        return 0

    allowed = gate.get("allowed_groups") or []
    speak = gate.get("speak_groups") or []
    only = gate.get("at_only_groups") or gate.get("only_when_addressed") or []

    lines = []
    for gid in speak:
        lines.append('[gate.chats."group:%d"]' % int(gid))
        lines.append('preset = "loose"                 # 参与感强：每条都交给她判断')
        lines.append("")
    for gid in only:
        if int(gid) in [int(x) for x in speak]:
            continue
        lines.append('[gate.chats."group:%d"]' % int(gid))
        lines.append('preset = "strict"                # 只理被叫到的消息')
        lines.append("")
    lines.append('# 豹群这类要"完全不留痕"的会话，加这一段：')
    lines.append('# [gate.chats."group:<群号>"]')
    lines.append('# preset = "strict"')
    lines.append('# memory = "off"        # 不读也不写记忆，连 recent 都不落盘')
    lines.append('# history = false       # 上下文里不带聊天记录')
    lines.append('# continuation = false  # 聊完不追')

    block = HEADER % {"allowed": str(allowed).replace("'", '"'),
                      "chats": "\n".join(lines)}

    # 用新段落整体替换 [gate] 段（到下一个 [ 开头的顶级段为止）
    new_text, n = re.subn(r"(?ms)^\[gate\]\n.*?(?=^\[(?!gate)|\Z)", block + "\n\n", text)
    if not n:
        print("找不到 [gate] 段")
        return 1
    print("=== 新 [gate] 段 ===")
    print(block)
    if a.dry:
        print("（dry-run，没写）")
        return 0

    bak = path + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, bak)
    tmp = path + ".tmp"
    open(tmp, "w", encoding="utf-8").write(new_text)
    os.replace(tmp, path)
    print("已写入 %s（备份 %s）" % (path, bak))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
