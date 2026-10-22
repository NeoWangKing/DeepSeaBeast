"""这个群常用哪些表情 —— 核心侧的计数。

分工：
  adapter 知道"平台有哪些表情"（静态清单，293 个）
  核心知道"这个群的人爱用哪几个"（动态计数，用于提示词里的 top-12）

只统计**别人发的**，不统计她自己发的——否则她会自己强化自己的偏好，越用越野。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("faces")

TOP_N = 12
# 没数据时的兜底：按"人类日常最常用"排的
DEFAULT_TOP = ["呲牙", "偷笑", "流泪", "坏笑", "托腮", "酷", "白眼", "抠鼻",
               "捂脸", "裂开", "苦涩", "旺柴"]


class FaceStats:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root) / "scope"
        self._cache: dict[str, dict[str, int]] = {}

    def _path(self, scope_key: str) -> Path:
        return self.root / scope_key / "faces.json"

    def load(self, scope_key: str) -> dict[str, int]:
        if scope_key in self._cache:
            return self._cache[scope_key]
        data: dict[str, int] = {}
        try:
            raw = json.loads(self._path(scope_key).read_text(encoding="utf-8"))
            data = {str(k): int(v) for k, v in (raw.get("counts") or {}).items()}
        except Exception:
            data = {}
        self._cache[scope_key] = data
        return data

    def count(self, scope_key: str, names: list[str]) -> None:
        if not names:
            return
        data = self.load(scope_key)
        for n in names:
            n = str(n or "").strip()
            if n:
                data[n] = data.get(n, 0) + 1
        self._flush(scope_key)

    def top(self, scope_key: str, n: int = TOP_N,
            fallback: list[str] | None = None) -> list[str]:
        data = self.load(scope_key)
        ranked = sorted(data.items(), key=lambda kv: (-kv[1], kv[0]))
        out = [k for k, _v in ranked[:n]]
        if len(out) < n:
            for name in (fallback or DEFAULT_TOP):
                if name not in out:
                    out.append(name)
                if len(out) >= n:
                    break
        return out[:n]

    def _flush(self, scope_key: str) -> None:
        try:
            p = self._path(scope_key)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"counts": self._cache.get(scope_key, {})},
                                      ensure_ascii=False, indent=0), encoding="utf-8")
            os.replace(tmp, p)
        except Exception as e:
            log.debug("表情计数落盘失败：%r", e)
