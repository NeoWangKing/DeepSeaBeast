#!/usr/bin/env python3
"""文件插件自测：沙箱越界防护、写读删、发送意图。"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


def main() -> int:
    td = tempfile.mkdtemp()
    os.environ["DEEPSEA_PLUGIN_DATA"] = td
    spec = importlib.util.spec_from_file_location("files", "plugins/files/main.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    sandbox = os.path.join(td, "files")

    print("== 写文件 ==")
    r = m.do_write({"name": "笔记.md", "text": "# 标题\n正文"})
    ck("写成功", r.get("ok") is True, str(r)[:60])
    sends = (r.get("data") or {}).get("send") or []
    ck("返回了发送意图", len(sends) == 1 and sends[0]["kind"] == "file", str(sends)[:80])
    ck("文件真的写了", os.path.isfile(os.path.join(sandbox, "笔记.md")))
    ck("内容对", open(os.path.join(sandbox, "笔记.md"), encoding="utf-8").read() == "# 标题\n正文")

    print()
    print("== 不发出去也能写 ==")
    r2 = m.do_write({"name": "only_save.txt", "text": "x", "send": False})
    ck("send=False 时不带意图", "send" not in json.dumps(r2.get("data") or {}))

    print()
    print("== 沙箱防护 ==")
    for bad in ("../逃出去.txt", "/etc/passwd", "a/b.txt", "..", ""):
        try:
            r = m.do_write({"name": bad, "text": "x"})
            ck("拒绝非法名 %r" % bad, r.get("ok") is False or "send" in json.dumps(r),
               str(r)[:50])
        except ValueError:
            ck("拒绝非法名 %r" % bad, True)
    ck("沙箱外的文件没被创建", not os.path.exists(os.path.join(td, "逃出去.txt")))

    print()
    print("== 读 / 列 / 删 ==")
    r3 = m.do_read({"name": "笔记.md"})
    ck("能读回来", (r3.get("data") or {}).get("text") == "# 标题\n正文", str(r3)[:50])
    r4 = m.do_list({})
    ck("列表里有文件", "笔记.md" in (r4.get("data") or {}).get("text", ""), str(r4)[:60])
    r5 = m.do_delete({"name": "笔记.md"})
    ck("能删", r5.get("ok") is True, str(r5)[:40])
    ck("删掉了", not os.path.exists(os.path.join(sandbox, "笔记.md")))

    print()
    print("== 大小限制 ==")
    r6 = m.do_write({"name": "big.txt", "text": "x" * (m.MAX_WRITE + 10)})
    ck("太大的拒绝", r6.get("ok") is False, str(r6)[:50])

    print()
    print("== 工具声明 ==")
    names = {t["name"] for t in m.TOOLS}
    ck("四个工具", names == {"write_file", "read_file", "list_files", "delete_file"}, str(names))
    wf = next(t for t in m.TOOLS if t["name"] == "write_file")
    ck("写文件要求 file 能力", wf.get("requires") == ["file"], str(wf.get("requires")))
    ck("写文件算对外动作", wf.get("kind") == "action", str(wf.get("kind")))

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
