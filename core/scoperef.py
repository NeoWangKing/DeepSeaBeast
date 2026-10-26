"""会话引用：核心内部只认"不透明引用"，具体身份只在渲染提示词时出现。

为什么要这样：
- 核心不该知道 `g100000001` 是群号——那是通道的事；
- 数据目录里不该散落平台 id；
- 不同通道用同一个原始 key（比如两个本地终端都用 "cli"）也不会撞车，
  因为引用里混了通道名。

`ChatRef.ref` 由核心算（adapter 不管），同一个（通道, 原始 key）永远得到同一个引用。
反过来要查"这是哪个群"，看 `data/scopes.json` 的登记表——只有人需要它，核心逻辑不需要。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path


def scope_ref(channel_name: str, raw_key: str) -> str:
    """（通道名, 原始会话 key）→ 不透明引用。"""
    seed = ("%s\x00%s" % (str(channel_name or ""), str(raw_key or ""))).encode("utf-8")
    return "s" + hashlib.blake2b(seed, digest_size=8).hexdigest()


class ScopeRegistry:
    """引用 → 人话身份（通道、原始 key、类别、名字），只为排查用。"""

    def __init__(self, data_dir: str | Path) -> None:
        self.path = Path(data_dir) / "scopes.json"
        self._cache: dict[str, dict] | None = None

    def load(self) -> dict[str, dict]:
        if self._cache is None:
            try:
                self._cache = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._cache = {}
        return self._cache

    def remember(self, ref: str, channel: str, raw_key: str, kind: str = "",
                 label: str = "", name: str = "") -> None:
        d = self.load()
        cur = d.get(ref) or {}
        new = {"channel": channel, "key": raw_key, "kind": kind or cur.get("kind", ""),
               "label": label or cur.get("label", ""), "name": name or cur.get("name", ""),
               "first_seen": cur.get("first_seen") or time.time(),
               "last_seen": time.time()}
        if cur == new:
            return
        d[ref] = new
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=0), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            pass

    def describe(self, ref: str) -> str:
        d = self.load().get(ref) or {}
        if not d:
            return ref
        bits = [d.get("channel", ""), d.get("label") or d.get("kind", ""), d.get("name", "")]
        return "%s（%s）" % (ref[:8], " / ".join(b for b in bits if b))
