#!/usr/bin/env python3
"""最小插件示例：stdio + 行分隔 JSON。

协议见 core/plugin/__init__.py。日志一律写 stderr，stdout 只走协议。
"""

from __future__ import annotations

import json
import sys
import time

TOOLS = [
    {
        "name": "echo_say",
        "description": "把传入的文本原样返回（示例插件）",
        "parameters": {"type": "object",
                       "properties": {"text": {"type": "string"}},
                       "required": ["text"]},
    },
    {
        "name": "echo_slow",
        "description": "故意睡 N 秒，用来测超时",
        "parameters": {"type": "object",
                       "properties": {"seconds": {"type": "number"}},
                       "required": ["seconds"]},
    },
]


def send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def log(msg: str) -> None:
    sys.stderr.write("[echo_plugin] %s\n" % msg)
    sys.stderr.flush()


def handle(method: str, params: dict):
    if method == "hello":
        return {"name": "echo_plugin", "version": "1.0", "tools": TOOLS}
    if method == "call_tool":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo_say":
            return {"text": str(args.get("text") or "")}
        if name == "echo_slow":
            sec = float(args.get("seconds") or 1)
            time.sleep(max(0.0, sec))
            return {"slept": sec}
        raise ValueError("没有这个工具：%s" % name)
    raise ValueError("不认识的方法：%s" % method)


def main() -> None:
    log("started")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            log("非 JSON 输入，忽略")
            continue
        mid = msg.get("id")
        try:
            result = handle(str(msg.get("method") or ""), msg.get("params") or {})
            send({"id": mid, "result": result})
        except Exception as e:
            send({"id": mid, "error": str(e)})


if __name__ == "__main__":
    main()
