#!/usr/bin/env python3
"""文件插件：让她**能写出东西来**，并且直接发出去。

以前她只能收文件，不能产出文件。现在：
  write_file(name, text)  → 写进沙箱，并把文件发出去（一步到位）
  read_file(name)         → 读回沙箱里的文本
  list_files()            → 看看沙箱里有什么
  delete_file(name)       → 删掉

**沙箱**：所有文件都在 $DEEPSEA_PLUGIN_DATA/files/ 下面，`..`、绝对路径一律拒绝。
发送走核心的 file 意图（`{"send": [{"kind": "file", "path": ...}]}`），
通道有没有"发文件"能力由核心判断——插件不关心。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

DATA = os.environ.get("DEEPSEA_PLUGIN_DATA") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data")
SANDBOX = os.path.join(DATA, "files")
MAX_WRITE = 512 * 1024          # 单文件写 512KB 上限
MAX_READ = 200 * 1024           # 读回来最多 200KB
NAME_RE = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff.-]{0,60}$")


def log(msg: str) -> None:
    sys.stderr.write("[files] %s\n" % msg)
    sys.stderr.flush()


def safe_name(name: str) -> str:
    """只允许沙箱内的简单文件名：拒绝路径分隔符、..、绝对路径。"""
    n = str(name or "").strip()
    n = n.replace("\\", "/").split("/")[-1]      # 去掉任何目录部分
    n = n.strip(". ")
    if not n or n in (".", "..") or not NAME_RE.match(n):
        raise ValueError("文件名不合法：%r" % name)
    return n


def path_of(name: str) -> str:
    os.makedirs(SANDBOX, exist_ok=True)
    p = os.path.join(SANDBOX, safe_name(name))
    real = os.path.realpath(p)
    if not real.startswith(os.path.realpath(SANDBOX) + os.sep):
        raise ValueError("越界了")
    return real


def do_write(args: dict) -> dict:
    name = str(args.get("name") or "").strip()
    text = str(args.get("text") or "")
    if not name:
        return {"ok": False, "error": "要给个文件名"}
    if len(text.encode("utf-8")) > MAX_WRITE:
        return {"ok": False, "error": "内容太大（上限 %d KB）" % (MAX_WRITE // 1024)}
    try:
        p = path_of(name)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    note = "写好了 %s（%d 字节）" % (os.path.basename(p), len(text.encode("utf-8")))
    if args.get("send", True):
        return {"ok": True, "data": {"send": [{"kind": "file", "path": p,
                                               "name": os.path.basename(p)}],
                                     "text": note + "，发给你了"}}
    return {"ok": True, "data": {"text": note}}


def do_read(args: dict) -> dict:
    try:
        p = path_of(str(args.get("name") or ""))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not os.path.isfile(p):
        return {"ok": False, "error": "没有这个文件：%s" % os.path.basename(p)}
    if os.path.getsize(p) > MAX_READ:
        return {"ok": False, "error": "文件太大，读不回来"}
    with open(p, encoding="utf-8", errors="replace") as f:
        return {"ok": True, "data": {"text": f.read()}}


def do_list(args: dict) -> dict:
    os.makedirs(SANDBOX, exist_ok=True)
    rows = []
    for f in sorted(os.listdir(SANDBOX)):
        p = os.path.join(SANDBOX, f)
        if os.path.isfile(p):
            rows.append("%s  %d 字节" % (f, os.path.getsize(p)))
    if not rows:
        return {"ok": True, "data": {"text": "沙箱里是空的"}}
    return {"ok": True, "data": {"text": "沙箱里 %d 个文件：\n%s" % (len(rows), "\n".join(rows[:30]))}}


def do_delete(args: dict) -> dict:
    try:
        p = path_of(str(args.get("name") or ""))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not os.path.isfile(p):
        return {"ok": False, "error": "没有这个文件"}
    os.unlink(p)
    return {"ok": True, "data": {"text": "删了 %s" % os.path.basename(p)}}


TOOLS = [
    {"name": "write_file", "label": "写文件发出去", "kind": "action",
     "description": "把内容写成一个文件并发给对方（.txt/.md/.csv 这类纯文本）。"
                    "适合「整理成文档」「写成文件」这类要求。",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string", "description": "文件名，例如 笔记.md"},
         "text": {"type": "string", "description": "文件内容"},
         "send": {"type": "boolean", "description": "要不要顺便发出去，默认 true"}},
         "required": ["name", "text"]},
     "requires": ["file"]},
    {"name": "read_file", "label": "读沙箱文件", "kind": "query",
     "description": "读回自己之前写过的一个文本文件。",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                    "required": ["name"]}},
    {"name": "list_files", "label": "看沙箱文件", "kind": "query",
     "description": "看看自己写过哪些文件。",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "delete_file", "label": "删沙箱文件", "kind": "control",
     "description": "删掉自己写过的某个文件。",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                    "required": ["name"]}},
]


def handle(method: str, params: dict):
    if method == "hello":
        log("started, sandbox=%s" % SANDBOX)
        return {"name": "files", "version": "1.0", "protocol": 1,
                "capabilities": ["tools"], "events": [], "jobs": [], "tools": TOOLS}
    if method == "call_tool":
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        if name == "write_file":
            return do_write(args)
        if name == "read_file":
            return do_read(args)
        if name == "list_files":
            return do_list(args)
        if name == "delete_file":
            return do_delete(args)
        return {"ok": False, "error": "没有这个工具：%s" % name}
    if method == "ping":
        return {"pong": True}
    if method == "shutdown":
        return {"ok": True}
    raise ValueError("不认识的方法：%s" % method)


def main() -> None:
    os.makedirs(SANDBOX, exist_ok=True)
    log("started, sandbox=%s" % SANDBOX)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = str(msg.get("method") or "")
        if method == "event":
            continue
        try:
            out = {"id": msg.get("id"), "result": handle(method, msg.get("params") or {})}
        except Exception as e:
            out = {"id": msg.get("id"), "error": str(e)}
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if method == "shutdown":
            break


if __name__ == "__main__":
    main()
