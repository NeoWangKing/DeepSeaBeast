"""QQ 表情：id ↔ 名字 ↔ 行内 token。

只属于这个 adapter——核心不认识 QQ 的表情体系。
核心看到的永远是文本：`[表情:狗头]`；发出去时才换成 OneBot 的 face 段。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("onebot.faces")

_PATH = Path(__file__).with_name("faces.json")
_TOKEN_RE = re.compile(r"\[表情:([^\[\]:]{1,12})\]")

_names: dict[str, str] | None = None      # id → 名字
_ids: dict[str, str] | None = None        # 名字 → id


def _load() -> None:
    global _names, _ids
    if _names is not None:
        return
    try:
        raw = json.loads(_PATH.read_text(encoding="utf-8"))
        _names = {str(k): str(v) for k, v in (raw.get("names") or {}).items()}
    except Exception as e:
        log.warning("读不到表情表 %s：%r", _PATH, e)
        _names = {}
    _ids = {v: k for k, v in _names.items()}
    log.info("载入 QQ 表情 %d 个", len(_names))


def all_names() -> list[str]:
    _load()
    return list(_ids or {})


def name_of(face_id: str) -> str:
    _load()
    return (_names or {}).get(str(face_id), "")


def id_of(name: str) -> str:
    _load()
    return (_ids or {}).get(str(name).strip().lstrip("/"), "")


def to_token(face_id: str) -> str:
    """收进来的 face id → 核心看到的文本。"""
    n = name_of(face_id)
    return "[表情:%s]" % (n or face_id)


def split_text(text: str) -> list[tuple[str, dict]]:
    """把一段文字拆成 [(kind, data)]：把 `[表情:名字]` 换成 face 段。

    返回的 kind 只有 "text" 和 "face"；不认识的名字原样留在文字里。
    """
    _load()
    out: list[tuple[str, dict]] = []
    pos = 0
    for m in _TOKEN_RE.finditer(text or ""):
        name = m.group(1)
        fid = id_of(name)
        if not fid:
            continue                     # 不认识的写法：当普通文字
        if m.start() > pos:
            out.append(("text", {"text": text[pos:m.start()]}))
        out.append(("face", {"id": fid}))
        pos = m.end()
    if pos < len(text or ""):
        out.append(("text", {"text": text[pos:]}))
    return out or [("text", {"text": text or ""})]
