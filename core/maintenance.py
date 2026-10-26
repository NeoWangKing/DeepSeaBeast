"""运维小工具：看有哪些会话、清掉某个会话的上下文。

为什么需要：她答错一次以后，错误回答会被写进 recent，后面几轮一直拿它当参考，
越聊越偏。以前只能手动删文件，现在一条命令。

**只清"对话记忆"，不动待办**（待办是她答应你的事，不该因为清上下文消失）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path


def _norm_key(data_dir: Path, key: str) -> tuple[str, str, str]:
    """把引用或原始 key 归一成 (opaque_ref, 原始 key, 错误原因)。

    核心不认识平台：原始 key → 引用 靠登记表 `data/scopes.json` 反查，
    查不到就让用户直接给 s 开头的引用。
    """
    raw = str(key or "").strip()
    if raw.startswith("s"):
        return raw, "", ""
    try:
        reg = json.loads((data_dir / "scopes.json").read_text(encoding="utf-8"))
    except Exception:
        reg = {}
    for ref, info in (reg or {}).items():
        if not isinstance(info, dict):
            continue
        if str(info.get("key") or "") == raw or str(info.get("id") or "") == raw:
            return str(ref), raw, ""
    return "", raw, ("登记表里没有这个会话（%s）。用 --list-scopes 看可用的引用，"
                     "或直接传 s 开头的引用" % raw)


def _targets(data_dir: Path, ref: str, raw: str) -> list[Path]:
    """这个会话在磁盘上的所有痕迹。"""
    out = [
        data_dir / "recent" / ("%s.json" % ref),
        data_dir / "memory" / "scope" / ref,
        data_dir / "plugins" / "memory" / "spaces" / ("%s.json" % ref),
        data_dir / "plugins" / "memory" / "spaces" / ref,
    ]
    if raw:      # 旧格式（还没迁到引用时的目录名）
        out += [data_dir / "recent" / ("%s.json" % raw),
                data_dir / "memory" / "scope" / raw]
    return out


def list_scopes(data_dir: str | Path = "data") -> list[dict]:
    """列出磁盘上留过痕迹的会话。"""
    d = Path(data_dir)
    rows: dict[str, dict] = {}
    reg = {}
    try:
        reg = json.loads((d / "scopes.json").read_text(encoding="utf-8"))
    except Exception:
        reg = {}

    def _note(ref: str, what: str) -> None:
        info = reg.get(ref) if isinstance(reg, dict) else None
        rows.setdefault(ref, {"ref": ref, "what": [], "info": info or {}})["what"].append(what)

    for p in (d / "recent").glob("*.json"):
        _note(p.stem, "recent")
    for p in (d / "memory" / "scope").glob("*"):
        if p.is_dir():
            _note(p.name, "记忆")
    for p in (d / "plugins" / "memory" / "spaces").glob("*.json"):
        _note(p.stem, "插件记忆")
    out = []
    for ref, row in rows.items():
        info = row.get("info") or {}
        label = info.get("label") or info.get("name") or ""
        out.append({"ref": ref, "label": label, "what": sorted(set(row["what"])),
                    "raw": info.get("key") or ""})
    return sorted(out, key=lambda x: x["ref"])


def reset_scope(data_dir: str | Path, key: str, dry: bool = False) -> dict:
    """清掉一个会话的上下文（recent + 记忆）。返回做了什么。"""
    d = Path(data_dir)
    ref, raw, err = _norm_key(d, key)
    if err:
        return {"ref": "", "removed": [], "dry": dry, "error": err}
    removed: list[str] = []
    for p in _targets(d, ref, raw):
        if not p.exists():
            continue
        if not dry:
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
            except OSError as e:
                removed.append("%s（删不掉：%r）" % (p, e))
                continue
        removed.append(str(p))
    return {"ref": ref, "removed": removed, "dry": dry, "error": ""}
