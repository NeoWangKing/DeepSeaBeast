#!/usr/bin/env python3
"""把旧实现的表情包库（index.json + 图片）迁到 stickers 插件的数据目录。

旧：/opt/astrbot/data/plugins/qq_peak_gate/data/stickers/
新：/opt/deepsea/data/plugins/stickers/library/  + index.json

保留 desc / tags / group / used / last_used —— desc 是当年模型写的描述，最值钱。
用法：python3 002_import_stickers.py [--dry]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil

OLD = "/opt/astrbot/data/plugins/qq_peak_gate/data/stickers"
NEW = "/opt/deepsea/data/plugins/stickers"
LIB = os.path.join(NEW, "library")
INDEX = os.path.join(NEW, "index.json")
SKIP = {"_rejected", "review.html", "index.json"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(OLD):
        print("找不到旧库：%s" % OLD)
        return 1
    os.makedirs(LIB, exist_ok=True)

    try:
        old = json.load(open(os.path.join(OLD, "index.json"), encoding="utf-8"))
        items = list(old.get("items") or [])
    except Exception as e:
        print("读旧索引失败：%r" % (e,))
        items = []

    kept, copied, missing = [], 0, 0
    for it in items:
        f = str(it.get("file") or "")
        if not f or not os.path.isfile(os.path.join(OLD, f)):
            missing += 1
            continue
        dst = os.path.join(LIB, f)
        if not os.path.exists(dst):
            if not a.dry:
                shutil.copy2(os.path.join(OLD, f), dst)
            copied += 1
        kept.append({
            "id": it.get("id") or f.split(".")[0],
            "file": f,
            "desc": it.get("desc") or "",
            "tags": list(it.get("tags") or []),
            "group": it.get("group") or "",
            "size": int(it.get("size") or 0),
            "added_at": it.get("added_at") or 0,
            "used": int(it.get("used") or 0),
            "last_used": it.get("last_used") or 0,
        })

    # 旧库里没登记但存在的图片也收进来（desc 空，等 note_sticker 补）
    known = {x["file"] for x in kept}
    for f in sorted(os.listdir(OLD)):
        if f in SKIP or f in known or not f.lower().endswith((".gif", ".png", ".jpg", ".jpeg", ".webp")):
            continue
        if not a.dry:
            dst = os.path.join(LIB, f)
            if not os.path.exists(dst):
                shutil.copy2(os.path.join(OLD, f), dst)
        kept.append({"id": f.split(".")[0], "file": f, "desc": "", "tags": [],
                     "group": "", "size": os.path.getsize(os.path.join(OLD, f)),
                     "added_at": 0, "used": 0, "last_used": 0})
        copied += 1

    print("索引 %d 条（原 %d，缺失文件 %d），复制 %d 个文件"
          % (len(kept), len(items), missing, copied))
    print("带描述的: %d / %d" % (sum(1 for x in kept if x["desc"]), len(kept)))

    if not a.dry:
        tmp = INDEX + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"updated_at": 0, "items": kept}, f, ensure_ascii=False,
                      separators=(",", ":"))
        os.replace(tmp, INDEX)
        print("写出 %s" % INDEX)
    else:
        print("（dry-run，没写文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
