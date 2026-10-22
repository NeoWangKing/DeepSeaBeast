#!/usr/bin/env python3
"""参考插件：协议 v1 的最小实现。

演示：工具、事件订阅、数据目录、健康检查、优雅退出。
日志写 stderr，stdout 只走协议。协议见 docs/PLUGIN_API.md
"""

from __future__ import annotations

import json
import os
import sys
import time

START = time.time()
DATA = os.environ.get("DEEPSEA_PLUGIN_DATA", ".")
EVENTS_FILE = os.path.join(DATA, "events.jsonl")

TOOLS = [
    {
        "name": "echo_say",
        "description": "把传入的文本原样返回（示例插件）",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                       "required": ["text"]},
    },
    {
        "name": "echo_slow",
        "description": "故意睡 N 秒，用来测超时",
        "parameters": {"type": "object", "properties": {"seconds": {"type": "number"}},
                       "required": ["seconds"]},
    },
]


def log(msg: str) -> None:
    sys.stderr.write("[echo_plugin] %s\n" % msg)
    sys.stderr.flush()


def send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def record_event(event: str, data: dict) -> None:
    """把收到的事件落盘——自测里用它证明事件真的到了插件。"""
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": event, "data": data}, ensure_ascii=False) + "\n")
    except Exception as e:
        log("写事件失败：%r" % (e,))


JOBS = [
    {"name": "heartbeat", "interval_sec": 60, "timeout": 10,
     "description": "每分钟在数据目录里记一笔，演示后台任务"},
]


def handle(method: str, params: dict):
    if method == "hello":
        return {"name": "echo_plugin", "version": "2.1", "protocol": 1,
                "capabilities": ["tools", "events", "jobs"],
                "events": ["message.received", "reply.sent"],
                "jobs": JOBS,
                "tools": TOOLS}
    if method == "run_job":
        job = str(params.get("job") or "")
        if job == "heartbeat":
            beat = os.path.join(DATA, "heartbeat.log")
            os.makedirs(DATA, exist_ok=True)
            with open(beat, "a", encoding="utf-8") as f:
                f.write("%.0f\n" % time.time())
            return {"ok": True, "data": {"wrote": beat}}
        return {"ok": False, "error": "没有这个任务：%s" % job}
    if method == "ping":
        return {"pong": True, "uptime_sec": int(time.time() - START)}
    if method == "shutdown":
        log("收到 shutdown，退出")
        return {"ok": True}
    if method == "call_tool":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo_say":
            return {"ok": True, "data": {"text": str(args.get("text") or "")}}
        if name == "echo_slow":
            sec = float(args.get("seconds") or 1)
            time.sleep(max(0.0, sec))
            return {"ok": True, "data": {"slept": sec}}
        return {"ok": False, "error": "没有这个工具：%s" % name}
    raise ValueError("不认识的方法：%s" % method)


def main() -> None:
    log("started, data=%s" % DATA)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            log("非 JSON 输入，忽略")
            continue
        method = str(msg.get("method") or "")
        if method == "event":
            p = msg.get("params") or {}
            record_event(str(p.get("event") or ""), p.get("data") or {})
            continue                        # 事件是单向通知，不回包
        try:
            result = handle(method, msg.get("params") or {})
            send({"id": msg.get("id"), "result": result})
        except Exception as e:
            send({"id": msg.get("id"), "error": str(e)})
        if method == "shutdown":
            break


if __name__ == "__main__":
    main()
