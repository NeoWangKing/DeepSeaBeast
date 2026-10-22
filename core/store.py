"""回合上下文：每个作用域最近 N 条（落盘，重启能接上）。"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("store")

MAX_LINES = 20
MAX_CHARS = 200


class RecentStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._cache: dict[str, list[list]] = {}

    def _path(self, key: str) -> Path:
        return self.root / ("%s.json" % key)

    def get(self, key: str) -> list[tuple[str, str, int, float]]:
        if key not in self._cache:
            rows: list[list] = []
            try:
                data = json.loads(self._path(key).read_text(encoding="utf-8"))
                rows = list(data.get("rows") or [])[-MAX_LINES:]
            except Exception:
                rows = []
            self._cache[key] = rows
        return [tuple(r) for r in self._cache[key]]

    def append(self, key: str, who: str, text: str, uid: int = 0, ts: float = 0.0) -> None:
        rows = self._cache.setdefault(key, [])
        rows.append([who, str(text)[:MAX_CHARS], int(uid), float(ts)])
        del rows[:-MAX_LINES]
        self._flush(key)

    def _flush(self, key: str) -> None:
        try:
            p = self._path(key)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"rows": self._cache.get(key, [])},
                                      ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, p)
        except Exception as e:
            log.debug("recent 落盘失败：%r", e)
