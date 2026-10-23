"""待办：她自己答应下来的事，按会话分开存，跨回合、跨重启都活着。

**核心不认识任何平台**：会话只用一个不透明的 key（由通道的 scope_info() 给出），
核心永远不知道那是群号、私聊 QQ 还是某个 socket 连接。

存储：data/todos/<scope_key>.json
  {"meta": {"reminded": {"20261023": 1}},
   "items": [{id, text, created, due, status, source, done_at, reminded_at}]}
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path

OPEN = "open"
DONE = "done"
DROPPED = "dropped"


def _safe(key: str) -> str:
    """会话 key 变成安全的文件名（含怪字符就取哈希）。"""
    k = str(key or "").strip()
    if not k:
        return "_"
    if all(c.isalnum() or c in "-_." for c in k) and len(k) <= 64:
        return k
    return "h" + hashlib.sha1(k.encode("utf-8")).hexdigest()[:16]


@dataclass
class Todo:
    id: str
    text: str
    created: float
    due: float | None = None
    status: str = OPEN
    source: str = ""
    done_at: float | None = None
    reminded_at: float | None = None

    def is_open(self) -> bool:
        return self.status == OPEN


class TodoStore:
    def __init__(self, root: str | Path, max_open: int = 12, stale_days: int = 7,
                 remind_per_day: int = 3) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_open = int(max_open)
        self.stale_days = float(stale_days)
        self.remind_per_day = int(remind_per_day)

    # ── 读写 ──────────────────────────────────────────
    def _path(self, scope: str) -> Path:
        return self.root / (_safe(scope) + ".json")

    def load(self, scope: str) -> dict:
        p = self._path(scope)
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            d = {}
        d.setdefault("items", [])
        d.setdefault("meta", {})
        return d

    def save(self, scope: str, d: dict) -> None:
        p = self._path(scope)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
        os.replace(tmp, p)

    def open_items(self, scope: str) -> list[Todo]:
        out = [Todo(**{k: v for k, v in it.items() if k in Todo.__dataclass_fields__})
               for it in self.load(scope)["items"]]
        self._prune(scope, out)
        return [t for t in out if t.is_open()]

    def _prune(self, scope: str, items: list[Todo]) -> None:
        """太久没动的已完成/已放弃条目清掉，防止文件长大。"""
        cut = time.time() - self.stale_days * 86400
        keep = [t for t in items
                if t.is_open() or not t.done_at or t.done_at > cut]
        if len(keep) != len(items):
            d = self.load(scope)
            d["items"] = [asdict(t) for t in keep]
            self.save(scope, d)

    # ── 增删改 ─────────────────────────────────────────
    def add(self, scope: str, text: str, due: float | None = None,
            source: str = "") -> tuple[Todo | None, str]:
        """加一条待办。满了就拒绝，并说清现在手上压着多少。"""
        text = str(text or "").strip()
        if not text:
            return None, "待办内容是空的"
        opened = self.open_items(scope)
        if len(opened) >= self.max_open:
            return None, ("手头已经压着 %d 件事了（上限 %d），暂时接不了新的；"
                          "先把做完的用 done_todo 收掉" % (len(opened), self.max_open))
        d = self.load(scope)
        tid = "t%s" % uuid.uuid4().hex[:8]
        todo = Todo(id=tid, text=text[:200], created=time.time(),
                    due=due, source=source)
        d["items"].append(asdict(todo))
        self.save(scope, d)
        left = self.max_open - len(opened) - 1
        note = ""
        if left <= 2:
            note = "；手头还剩 %d 个名额" % left
        return todo, note

    def done(self, scope: str, tid: str) -> tuple[bool, str]:
        d = self.load(scope)
        hit = None
        for it in d["items"]:
            if it.get("id") == tid and it.get("status") == OPEN:
                it["status"] = DONE
                it["done_at"] = time.time()
                hit = it
                break
        if hit is None:
            return False, "没有这条待办：%s" % tid
        self.save(scope, d)
        left = sum(1 for it in d["items"] if it.get("status") == OPEN)
        if left == 0:
            return True, "收掉了，手上的事都做完了，可以接新的"
        return True, "收掉了，还剩 %d 件" % left

    def drop(self, scope: str, tid: str) -> tuple[bool, str]:
        d = self.load(scope)
        for it in d["items"]:
            if it.get("id") == tid and it.get("status") == OPEN:
                it["status"] = DROPPED
                it["done_at"] = time.time()
                self.save(scope, d)
                return True, "不做了：%s" % it.get("text", "")[:30]
        return False, "没有这条待办：%s" % tid

    # ── 到点提醒 ───────────────────────────────────────
    def due_now(self, now: float | None = None) -> list[tuple[str, Todo]]:
        """所有到点、还没提醒过的待办（跨会话）。"""
        now = time.time() if now is None else now
        out: list[tuple[str, Todo]] = []
        for p in sorted(self.root.glob("*.json")):
            scope = p.stem
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            for it in d.get("items") or []:
                if it.get("status") != OPEN:
                    continue
                due = it.get("due")
                if not due or float(due) > now:
                    continue
                if it.get("reminded_at"):
                    continue
                out.append((scope, Todo(**{k: v for k, v in it.items()
                                           if k in Todo.__dataclass_fields__})))
        return out

    def mark_reminded(self, scope: str, tid: str) -> bool:
        """记下"已经提醒过"，同一条不会重复打扰；顺带数一下今天的次数。"""
        d = self.load(scope)
        today = time.strftime("%Y%m%d")
        meta = d.setdefault("meta", {})
        used = int((meta.get("reminded") or {}).get(today) or 0)
        if used >= self.remind_per_day:
            return False
        for it in d["items"]:
            if it.get("id") == tid and it.get("status") == OPEN:
                it["reminded_at"] = time.time()
                meta.setdefault("reminded", {})[today] = used + 1
                self.save(scope, d)
                return True
        return False
