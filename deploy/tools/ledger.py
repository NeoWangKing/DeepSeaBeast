#!/usr/bin/env python3
"""部署账本：判断"这个文件是谁的"，让更新既不吞用户改动、也不留半新半旧。

借鉴 Nyxuri 的 file-preservation 契约（判定权归账本）：

  plan    对比 账本 / 磁盘 / 新 payload，输出三类报告
          · 用户改过的代码（会被新版本覆盖 —— 提示你备份）
          · 模板新增的文件
          · 磁盘上有、账本里没有的文件（来源不明 → 不碰）
  record  把这次铺设的文件哈希写进账本

**边界（和 Nyxuri 不同，这里是刻意的）**：
  `__custom__*` / `data/` / `.secrets/` 一律不碰（硬规则，不靠账本）；
  其余代码文件归"上游所有"，新版本覆盖旧版本——但会先报告你手动改过哪些。
  配置（*__custom__*）归用户所有：永远保留，只做"模板新增键"的差异提示。

用法：
  python3 ledger.py plan   --payload /tmp/src --dest /opt/deepsea
  python3 ledger.py record --payload /tmp/src --dest /opt/deepsea
  python3 ledger.py check-custom --custom /opt/deepsea/core__custom__.toml \\
                                --template /tmp/src/deploy/core.__custom__.toml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

LEDGER_REL = "deploy/state/deployed.json"
SKIP_DIRS = {"__pycache__", "data", ".secrets", ".git", ".venv", "node_modules"}
SKIP_PATHS = {"deploy/state"}          # 账本自己不算"代码文件"
SKIP_SUFFIX = (".pyc", ".log")


def is_user_owned(rel: str) -> bool:
    """用户的东西：一律不碰。"""
    if any(str(rel).startswith(p) for p in SKIP_PATHS):
        return True
    parts = Path(rel).parts
    if any(p in SKIP_DIRS for p in parts):
        return True
    return any("__custom__" in p for p in parts)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(SKIP_SUFFIX):
                continue
            p = Path(dirpath) / name
            rel = str(p.relative_to(root))
            if is_user_owned(rel):
                continue
            try:
                out[rel] = sha(p)
            except OSError:
                continue
    return out


def load_ledger(dest: Path) -> dict:
    try:
        return json.loads((dest / LEDGER_REL).read_text(encoding="utf-8"))
    except Exception:
        return {"version": 0, "files": {}}


def cmd_plan(args) -> int:
    payload, dest = Path(args.payload), Path(args.dest)
    ledger = load_ledger(dest).get("files") or {}
    new = walk(payload)
    old = walk(dest) if dest.exists() else {}

    modified = [r for r in new
                if r in old and r in ledger and old[r] != ledger[r] and old[r] != new[r]]
    added = [r for r in new if r not in old]
    unknown = [r for r in old if r not in ledger and r not in new]

    print("=== 部署计划 ===")
    print("新文件 %d，账本 %d 条" % (len(new), len(ledger)))
    if modified:
        print("\n⚠️ 你手改过的文件（这次会被新版本覆盖，先备份）：")
        for r in sorted(modified)[:20]:
            print("   " + r)
    if added:
        print("\n＋ 模板新增：%d 个" % len(added))
        for r in sorted(added)[:20]:
            print("   " + r)
    if unknown:
        print("\n？ 磁盘上有、账本里没有（不碰，来源不明）：%d 个" % len(unknown))
        for r in sorted(unknown)[:10]:
            print("   " + r)
    print("\n用户私有（不碰）：__custom__* / data/ / .secrets/")
    return 0


def cmd_prune(args) -> int:
    """删掉"账本里有、新 payload 里没有"的文件（引擎自己的领地）。

    只动账本登记的路径——用户在部署目录里自己放的文件，账本没有记录，永远不删。
    这样就不用 `rsync --delete`（那玩意会连用户文件一起铲）。
    """
    payload, dest = Path(args.payload), Path(args.dest)
    ledger = load_ledger(dest).get("files") or {}
    new = walk(payload)
    gone = [r for r in ledger if r not in new]
    removed = 0
    for rel in gone:
        p = dest / rel
        try:
            if p.is_file():
                p.unlink()
                removed += 1
            # 顺手清空目录（如果空了）
            parent = p.parent
            while parent != dest and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        except OSError:
            pass
    if removed:
        print("→ 清掉 %d 个旧模板文件（账本登记过、新版已不含）" % removed)
    return 0


def cmd_record(args) -> int:
    payload, dest = Path(args.payload), Path(args.dest)
    files = walk(payload)
    dest.mkdir(parents=True, exist_ok=True)
    p = dest / LEDGER_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "files": files}, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, p)
    print("账本已更新：%s（%d 条）" % (p, len(files)))
    return 0


def _flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        key = prefix + k
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            for i, item in enumerate(v):
                out.update(_flatten(item, "%s[%d]." % (key, i)))
        else:
            out[key] = str(v)
    return out


def cmd_check_custom(args) -> int:
    import tomllib

    try:
        custom = _flatten(tomllib.loads(Path(args.custom).read_text(encoding="utf-8")))
    except FileNotFoundError:
        print("找不到 %s（首次部署会从模板生成）" % args.custom)
        return 0
    except Exception as e:
        print("读不了 %s：%r" % (args.custom, e))
        return 1
    try:
        tmpl = _flatten(tomllib.loads(Path(args.template).read_text(encoding="utf-8")))
    except Exception as e:
        print("读不了模板：%r" % e)
        return 1

    missing = [k for k in tmpl if k not in custom]
    extra = [k for k in custom if k not in tmpl]
    print("=== 配置差异（你的 core__custom__.toml vs 模板）===")
    if missing:
        print("模板有、你没有的键（新功能可能默认不生效）：")
        for k in sorted(missing)[:30]:
            print("   %s = %s" % (k, tmpl[k]))
    if extra:
        print("你有、模板没有的键（可能是旧版残留，或自己加的）：")
        for k in sorted(extra)[:20]:
            print("   " + k)
    if not missing and not extra:
        print("跟模板一致，没有新增键。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "record", "prune"):
        s = sub.add_parser(name)
        s.add_argument("--payload", required=True)
        s.add_argument("--dest", required=True)
    s = sub.add_parser("check-custom")
    s.add_argument("--custom", required=True)
    s.add_argument("--template", required=True)
    args = ap.parse_args()
    return {"plan": cmd_plan, "record": cmd_record, "prune": cmd_prune,
            "check-custom": cmd_check_custom}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
