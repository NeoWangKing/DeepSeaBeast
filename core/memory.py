"""分层记忆：self（她是谁） + scope（这一处的人和事）。

self   —— 所有场景都带着：身份、关于主人的事、她的经验教训。**极少写**。
scope  —— 只在这个群/这个人这里：群印象、成员档案、黑话、表达库。后台任务写。

这里只管"读写文件 + 拼成文本"，不做任何理解（理解交给 memory 插件）。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .scope import Scope

log = logging.getLogger("memory")

SELF_PARTS = ("identity", "owner", "lessons")
_MAX_PART_CHARS = 1200
_MAX_ITEMS = 8


class MemoryStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.self_dir = self.root / "self"
        self.scope_dir = self.root / "scope"

    # ── self 层 ──
    def self_parts(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in SELF_PARTS:
            p = self.self_dir / ("%s.md" % name)
            try:
                text = p.read_text(encoding="utf-8").strip()
            except Exception:
                continue
            if text:
                out[name] = text[:_MAX_PART_CHARS]
        return out

    def write_self(self, name: str, text: str, append: bool = True) -> None:
        """写 self 层（只该由"主人明确要求"或每日自省触发）。"""
        name = name if name in SELF_PARTS else "owner"
        self.self_dir.mkdir(parents=True, exist_ok=True)
        p = self.self_dir / ("%s.md" % name)
        old = ""
        if append and p.exists():
            try:
                old = p.read_text(encoding="utf-8").rstrip() + "\n"
            except Exception:
                old = ""
        p.write_text(old + str(text).strip() + "\n", encoding="utf-8")

    # ── scope 层 ──
    def _scope_dir(self, scope: Scope) -> Path:
        return self.scope_dir / scope.dirname

    def scope_parts(self, scope: Scope) -> dict[str, str]:
        """把该作用域下的记忆拼成几段短文本（顺序固定，利于缓存）。"""
        d = self._scope_dir(scope)
        out: dict[str, str] = {}
        prof = d / "profile.md"
        try:
            t = prof.read_text(encoding="utf-8").strip()
            if t:
                out["profile"] = t[:_MAX_PART_CHARS]
        except Exception:
            pass
        # 成员档案：只取前 N 个，压成一行一个
        try:
            data = json.loads((d / "members.json").read_text(encoding="utf-8"))
            rows = []
            for uid, m in list((data.get("members") or {}).items())[:_MAX_ITEMS]:
                name = m.get("name") or uid
                prof_txt = str(m.get("profile") or "").strip()
                if prof_txt:
                    rows.append("%s：%s" % (name, prof_txt[:60]))
            if rows:
                out["members"] = "\n".join(rows)
        except Exception:
            pass
        # 黑话 / 表达库：只取有释义的少量
        try:
            jg = json.loads((d / "jargon.json").read_text(encoding="utf-8"))
            terms = [x for x in (jg.get("terms") or []) if x.get("meaning")][:_MAX_ITEMS]
            if terms:
                out["jargon"] = "、".join("%s（%s）" % (x.get("term"), str(x.get("meaning"))[:20])
                                          for x in terms)
        except Exception:
            pass
        try:
            ex = json.loads((d / "expressions.json").read_text(encoding="utf-8"))
            items = (ex.get("items") or [])[:_MAX_ITEMS]
            if items:
                out["expressions"] = " / ".join(str(x.get("surface") or "") for x in items if x.get("surface"))
        except Exception:
            pass
        return out

    def learn_scope(self, scope: Scope, kind: str, data) -> None:
        """后台学习任务写入（profile / members / jargon / expressions）。"""
        d = self._scope_dir(scope)
        d.mkdir(parents=True, exist_ok=True)
        if kind == "profile":
            (d / "profile.md").write_text(str(data).strip() + "\n", encoding="utf-8")
            return
        p = d / ("%s.json" % kind)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)

    def scopes(self) -> list[str]:
        try:
            return sorted(p.name for p in self.scope_dir.iterdir() if p.is_dir())
        except Exception:
            return []
