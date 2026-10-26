#!/usr/bin/env python3
"""把用户私有映射里的旧卡片路径改成新的代号路径。

旧单文件种子卡已改成目录卡（general/<代号>/），群卡也改成 groups/<代号>。
这个脚本只改 `data/plugins/personas/mapping__custom__.json` 里的**取值**，
不碰任何文件内容，也绝不删除映射项。

用法（在服务器上跑一次）：
  sudo -u deepsea /opt/deepsea/.venv/bin/python deploy/migrations/007_persona_card_paths.py
  ... --dry   先看会改什么
"""
from __future__ import annotations

import argparse
import json
import os

MAPPING = "/opt/deepsea/data/plugins/personas/mapping__custom__.json"

RENAMES = {
    "system_prompt.txt": "general/xiaojingyu",
    "system_prompt_friend.txt": "general/friend",
    "system_prompt_tool.txt": "general/tool",
    "system_prompt_private.txt": "general/private",
    "system_prompt_sharp.txt": "general/sharp",
    "private_warm.txt": "general/private-warm",
    "project_assistant.txt": "general/project-assistant",
    "terminal.txt": "general/terminal",
    "terminal_script.txt": "general/terminal-script",
    "1109525246": "groups/debug",
    "866103773.txt": "groups/miss-rai.txt",
}


def fix(v):
    return RENAMES.get(str(v), v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    path = os.path.expanduser(MAPPING)
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        print("读不到 %s：%r" % (path, e))
        return 1

    changed = []
    for key, val in list(d.items()):
        if isinstance(val, dict):
            for k, v in val.items():
                nv = fix(v)
                if nv != v:
                    changed.append("%s.%s: %s → %s" % (key, k, v, nv))
                    val[k] = nv
        elif isinstance(val, str):
            nv = fix(val)
            if nv != val:
                changed.append("%s: %s → %s" % (key, val, nv))
                d[key] = nv

    if not changed:
        print("没有需要改的路径")
        return 0
    print("要改 %d 处：" % len(changed))
    for c in changed:
        print("  " + c)
    if a.dry:
        print("（dry-run，没写）")
        return 0
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    print("已写入 %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
