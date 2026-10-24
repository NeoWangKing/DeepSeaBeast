#!/usr/bin/env python3
"""把旧插件的"表达库/黑话"迁到 jargon 插件。

旧：/opt/astrbot/data/plugins/qq_peak_gate/data/{jargon,expressions}/<群号>.json
新：/opt/deepsea/data/plugins/jargon/<会话引用>.json

只迁**有解释的**：
  · expressions 的 20 条句式（每条带 note 解释）→ patterns
  · jargon 的术语**全部没有释义**（16 条都是 pending），迁了也不该注入，跳过
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

OLD = "/opt/astrbot/data/plugins/qq_peak_gate/data"
NEW = "/opt/deepsea/data/plugins/jargon"


def scope_ref(channel: str, key: str) -> str:
    seed = ("%s\x00%s" % (channel, key)).encode("utf-8")
    return "s" + hashlib.blake2b(seed, digest_size=8).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default=OLD)
    ap.add_argument("--new", default=NEW)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.new, exist_ok=True)

    total_p = total_t = 0
    for folder, fname in (("expressions", "patterns"), ("jargon", "terms")):
        d = os.path.join(a.old, folder)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".json"):
                continue
            gid = f[:-5]
            ref = scope_ref("onebot", "g" + gid)
            try:
                raw = json.load(open(os.path.join(d, f), encoding="utf-8"))
            except Exception as e:
                print("跳过 %s：%r" % (f, e))
                continue
            out_path = os.path.join(a.new, ref + ".json")
            try:
                out = json.load(open(out_path, encoding="utf-8"))
            except Exception:
                out = {}
            out.setdefault("terms", [])
            out.setdefault("patterns", [])

            if fname == "patterns":
                have = {p.get("surface") for p in out["patterns"]}
                for it in raw.get("items") or []:
                    surface = str(it.get("surface") or "").strip()
                    note = str(it.get("note") or "").strip()
                    if not surface or not note or surface in have:
                        continue
                    out["patterns"].append({"surface": surface, "note": note,
                                            "kind": str(it.get("form_type") or ""),
                                            "evidence": str(it.get("evidence") or "")[:80],
                                            "created": it.get("created_at") or 0})
                    total_p += 1
            else:
                have = {t.get("term") for t in out["terms"]}
                for it in raw.get("terms") or []:
                    term = str(it.get("term") or "").strip()
                    meaning = str(it.get("meaning") or "").strip()
                    # 没有释义的术语不迁：注入出去只会变成噪音
                    if not term or not meaning or term in have:
                        continue
                    out["terms"].append({"term": term, "meaning": meaning,
                                         "kind": str(it.get("category") or ""),
                                         "created": it.get("created_at") or 0})
                    total_t += 1

            if a.dry:
                print("[dry] %s → %s：句式 %d，术语 %d"
                      % (f, ref, len(out["patterns"]), len(out["terms"])))
                continue
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))

    print("迁入句式 %d 条，术语 %d 条" % (total_p, total_t))
    if a.dry:
        print("（dry-run）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
