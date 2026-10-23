#!/usr/bin/env python3
"""把旧数据（用原始会话 key 命名）迁移成用"不透明引用"命名。

旧：data/recent/g1109525246.json、data/memory/scope/g1109525246/、
    data/plugins/memory/spaces/g1109525246.json …
新：同名文件/目录换成 s<hash>（= blake2b("通道名\\0原始key")）

通道推断：
  g*/p*  → onebot（QQ）
  cli    → cli    （本地命令行；注意 tcp/socket 以前共用 "cli" 这个 key，
                    历史数据只能归到 cli，之后它们各自有独立引用）

用法：python3 005_scope_refs.py [--data /opt/deepsea/data] [--dry]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys


def scope_ref(channel: str, key: str) -> str:
    seed = ("%s\x00%s" % (channel, key)).encode("utf-8")
    return "s" + hashlib.blake2b(seed, digest_size=8).hexdigest()


def guess_channel(key: str) -> str:
    if key.startswith(("g", "p")) and key[1:].isdigit():
        return "onebot"
    if key == "cli":
        return "cli"
    return "onebot"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/opt/deepsea/data")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    D = a.data
    moved: list[str] = []

    def migrate(path: str, is_dir: bool) -> None:
        name = os.path.basename(path).removesuffix(".json")
        ch = guess_channel(name)
        ref = scope_ref(ch, name)
        new = os.path.join(os.path.dirname(path),
                           ref + (".json" if path.endswith(".json") else ""))
        if os.path.exists(new):
            return
        moved.append("%s → %s（%s/%s）" % (os.path.relpath(path, D),
                                           os.path.basename(new), ch, name))
        if a.dry:
            return
        if is_dir:
            shutil.move(path, new)
        else:
            shutil.move(path, new)

    # recent/*.json
    for f in sorted(os.listdir(os.path.join(D, "recent")) if os.path.isdir(os.path.join(D, "recent")) else []):
        if f.endswith(".json"):
            migrate(os.path.join(D, "recent", f), False)

    # memory/scope/<key>/（含 faces.json）
    scope_dir = os.path.join(D, "memory", "scope")
    if os.path.isdir(scope_dir):
        for name in sorted(os.listdir(scope_dir)):
            p = os.path.join(scope_dir, name)
            if os.path.isdir(p) and not name.startswith(("s", "_")):
                migrate(p, True)

    # plugins/memory/spaces/<key>.json
    sp = os.path.join(D, "plugins", "memory", "spaces")
    if os.path.isdir(sp):
        for f in sorted(os.listdir(sp)):
            if f.endswith(".json"):
                migrate(os.path.join(sp, f), False)

    # 登记表：把迁移过的身份写进 scopes.json（只有人需要）
    reg_path = os.path.join(D, "scopes.json")
    reg = {}
    if os.path.exists(reg_path):
        try:
            reg = json.loads(open(reg_path, encoding="utf-8").read())
        except Exception:
            reg = {}
    for line in moved:
        # 从 moved 里解析回 key
        try:
            key = line.split("（")[-1].rstrip("）").split("/")[-1]
            ch = line.split("（")[-1].rstrip("）").split("/")[0]
        except Exception:
            continue
        ref = scope_ref(ch, key)
        reg.setdefault(ref, {"channel": ch, "key": key, "kind": "",
                             "label": "", "name": "", "first_seen": 0, "last_seen": 0,
                             "migrated": True})
    if moved and not a.dry:
        tmp = reg_path + ".tmp"
        open(tmp, "w", encoding="utf-8").write(json.dumps(reg, ensure_ascii=False, indent=0))
        os.replace(tmp, reg_path)

    print("迁移 %d 项：" % len(moved))
    for m in moved:
        print("  " + m)
    if a.dry:
        print("（dry-run，没动文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
