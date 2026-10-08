# -*- coding: utf-8 -*-
"""群激活管理：qqbot-activate [list | on <群号> | off <群号>]

新群默认未激活；只有主人 @ 她才会激活；主人说「关机」退回未激活。
这个工具是给运维手动兜底用的。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
P = os.path.join(ROOT, "data", "agent_active.json")


def load() -> dict:
    try:
        with open(P, encoding="utf-8") as f:
            d = json.load(f) or {}
        if isinstance(d.get("groups"), dict):
            return d
    except Exception:
        pass
    return {"groups": {}}


def save(d: dict) -> None:
    os.makedirs(os.path.dirname(P), exist_ok=True)
    tmp = P + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, P)


def main() -> int:
    a = sys.argv[1:]
    cmd = (a[0] if a else "list").lower()
    if cmd == "list":
        d = load()
        rows = d.get("groups") or {}
        if not rows:
            print("（没有任何已激活的群：所有群都处于未激活状态）")
        for g, v in sorted(rows.items()):
            print("  %-14s %s  by=%s  %s" % (g, "已激活" if v.get("on") else "未激活",
                                            v.get("by") or "-",
                                            time.strftime("%m-%d %H:%M", time.localtime(int(v.get("ts") or 0)))))
        return 0
    if cmd in ("on", "off") and len(a) >= 2:
        gid = str(a[1]).strip()
        d = load()
        d.setdefault("groups", {})[gid] = {"on": cmd == "on", "by": "cli", "ts": int(time.time())}
        save(d)
        print("群 %s → %s" % (gid, "已激活" if cmd == "on" else "未激活"))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
