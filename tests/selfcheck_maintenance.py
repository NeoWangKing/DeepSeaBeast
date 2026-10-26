#!/usr/bin/env python3
"""运维命令自测：列出会话痕迹、清掉某个会话的上下文（不动待办）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.maintenance import list_scopes, reset_scope  # noqa: E402
from core.scoperef import scope_ref  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


def main() -> int:
    d = Path(tempfile.mkdtemp())
    ref = scope_ref("onebot", "g100000001")
    (d / "recent").mkdir(parents=True)
    (d / "recent" / ("%s.json" % ref)).write_text(json.dumps([["a", "b", 1, 2.0]]), encoding="utf-8")
    (d / "memory" / "scope" / ref).mkdir(parents=True)
    (d / "memory" / "scope" / ref / "impression.md").write_text("印象", encoding="utf-8")
    (d / "plugins" / "memory" / "spaces").mkdir(parents=True)
    (d / "plugins" / "memory" / "spaces" / ("%s.json" % ref)).write_text("{}", encoding="utf-8")
    (d / "todos").mkdir(parents=True)
    (d / "todos" / ("%s.json" % ref)).write_text('{"items": [{"status": "open"}]}', encoding="utf-8")
    (d / "scopes.json").write_text(json.dumps({ref: {"label": "QQ 群", "name": "测试群",
                                                     "key": "g100000001"}}, ensure_ascii=False),
                                   encoding="utf-8")

    print("== 列会话 ==")
    rows = list_scopes(d)
    ck("列出来了", len(rows) == 1 and rows[0]["ref"] == ref, str(rows))
    ck("标了痕迹种类", set(rows[0]["what"]) == {"recent", "记忆", "插件记忆"}, str(rows[0]["what"]))
    ck("带人话标签", rows[0]["label"] == "QQ 群", str(rows[0]["label"]))

    print()
    print("== dry-run 不删 ==")
    r = reset_scope(d, ref, dry=True)
    ck("列出了会删的", len(r["removed"]) == 3, str(len(r["removed"])))
    ck("dry-run 后文件还在", (d / "recent" / ("%s.json" % ref)).exists())

    print()
    print("== 真删 ==")
    r2 = reset_scope(d, "g100000001")     # 用原始 key 也能定位
    ck("引用算对了", r2["ref"] == ref, r2["ref"])
    ck("recent 删了", not (d / "recent" / ("%s.json" % ref)).exists())
    ck("记忆目录删了", not (d / "memory" / "scope" / ref).exists())
    ck("插件记忆删了", not (d / "plugins" / "memory" / "spaces" / ("%s.json" % ref)).exists())
    ck("待办**没被动**", (d / "todos" / ("%s.json" % ref)).exists())

    print()
    print("== 清完再列 ==")
    ck("列表变空了", [x for x in list_scopes(d) if x["ref"] == ref] == [],
       str(list_scopes(d)))

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
