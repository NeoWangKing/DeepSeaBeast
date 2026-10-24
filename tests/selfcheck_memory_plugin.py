#!/usr/bin/env python3
"""memory 插件的端到端自测：事件攒素材 → 模型整理 → 落盘 → 注入。

用一个假的 OpenAI 兼容服务代替真模型，所以不花钱、不联网。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.plugin import PluginManager  # noqa: E402

FAIL: list[str] = []
REPLIES: list[str] = []          # 假模型按顺序返回的内容
SEEN: list[list] = []            # 记下每次请求的 messages


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


class FakeLLM(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        SEEN.append(body.get("messages") or [])
        reply = REPLIES.pop(0) if REPLIES else '{"ops": []}'
        out = json.dumps({"choices": [{"message": {"content": reply}}],
                          "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                    "total_tokens": 15}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):        # 别刷屏
        pass


async def main() -> int:
    srv = HTTPServer(("127.0.0.1", 0), FakeLLM)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    td = Path(tempfile.mkdtemp())
    keyfile = td / "key"
    keyfile.write_text("test-key\n", encoding="utf-8")
    py = str(ROOT / ".venv/bin/python")
    mgr = PluginManager(llm={"base_url": "http://127.0.0.1:%d/v1" % port,
                             "model": "fake", "api_key_file": str(keyfile),
                             "timeout": 10})
    try:
        await mgr.load({"name": "memory", "command": [py, "plugins/memory/main.py"],
                        "cwd": str(ROOT), "timeout": 20,
                        "data_dir": str(td / "data")}, base_dir=str(ROOT))
        ck("插件声明了 context/events/jobs",
           {"context", "events", "jobs"} <= mgr.capabilities["memory"])
        ck("订阅了 message.received", "message.received" in mgr.events["memory"])

        print()
        print("== 事件 → 素材缓冲 ==")
        for i in range(6):
            await mgr.emit("message.received", {
                "scope": {"kind": "group", "id": 1, "key": "g1"},
                "sender": {"id": 7, "name": "某人"},
                "text": "他又在折腾那个 Rust 项目了 %d" % i, "ts": 1000 + i})
        await asyncio.sleep(0.4)      # 事件是单向通知，给插件一点时间写盘
        buf = td / "data" / "buffer" / "g1.jsonl"
        ck("素材进 buffer", buf.exists() and len(buf.read_text().splitlines()) == 6,
           str(buf.exists()))

        print()
        print("== 后台任务：让模型整理 ==")
        REPLIES.append(json.dumps({"ops": [
            {"op": "add", "space": "session", "text": "本群某人在折腾 Rust 项目",
             "importance": 4, "tags": ["成员"]},
            {"op": "add", "space": "global", "text": "我不该在群里贴长代码",
             "importance": 5, "tags": ["经验"], "public": True},
        ]}, ensure_ascii=False))
        r = await mgr.run_job("memory", "consolidate")
        ck("consolidate 成功", r.get("ok") is True, str(r))
        ck("模型收到了已有记忆和对话", any("最近对话" in str(m) for m in SEEN[-1]))

        sess = json.loads((td / "data" / "spaces" / "g1.json").read_text(encoding="utf-8"))
        glob = json.loads((td / "data" / "spaces" / "global.json").read_text(encoding="utf-8"))
        ck("会话空间写了 1 条", len(sess["entries"]) == 1, str(sess["entries"][:1]))
        ck("全局空间写了 1 条", len(glob["entries"]) == 1, str(glob["entries"][:1]))
        ck("buffer 已清空", not buf.exists())

        print()
        print("== 注入：按相关性挑条目 ==")
        ctx = await mgr._procs["memory"].call("get_context", {
            "scope": {"kind": "group", "id": 1, "key": "g1"},
            "sender": {"id": 7, "name": "某人"},
            "channel": {"name": "onebot"},
            "message": "Rust 项目写到哪了"})
        text = "\n".join(ctx.get("knowledge") or [])
        ck("会话记忆被注入", "Rust 项目" in text, text[:60])
        ck("全局记忆也被注入", "长代码" in text, text[:80])

        print()
        print("== 硬约束：global 只收 public 条目 ==")
        REPLIES.append(json.dumps({"ops": [
            {"op": "add", "space": "global", "text": "灵其啊在写一个 Rust 项目", "importance": 5},
            {"op": "add", "space": "global", "text": "私聊里听说某人要换工作", "importance": 4},
            {"op": "add", "space": "session", "text": "灵其啊在写一个 Rust 项目", "importance": 5},
        ]}, ensure_ascii=False))
        await mgr.emit("message.received", {"scope": {"kind": "group", "id": 1, "key": "g1"},
                                            "sender": {"id": 7, "name": "某人"},
                                            "text": "我在写 Rust", "ts": 2100})
        await asyncio.sleep(0.3)
        await mgr.run_job("memory", "consolidate")
        glob2 = json.loads((td / "data" / "spaces" / "global.json").read_text(encoding="utf-8"))
        texts_global = [e["text"] for e in glob2["entries"]]
        ck("关于具体人的事没进 global", "Rust 项目" not in " ".join(texts_global),
           str(texts_global)[:70])
        ck("私聊听来的事没进 global", "换工作" not in " ".join(texts_global))
        ck("global 里仍然只有 pubic 的那条", len(glob2["entries"]) == 1, str(texts_global))
        sess3 = json.loads((td / "data" / "spaces" / "g1.json").read_text(encoding="utf-8"))
        ck("这类事留在本会话空间", any("Rust 项目" in e["text"] for e in sess3["entries"]),
           str([e["text"] for e in sess3["entries"]])[:70])

        print()
        print("== 别的会话看不到本会话的记忆 ==")
        other = await mgr._procs["memory"].call("get_context", {
            "scope": {"kind": "group", "id": 2, "key": "g2"},
            "sender": {"id": 9, "name": "别人"},
            "channel": {"name": "onebot"},
            "message": "Rust 项目"})
        other_text = "\n".join(other.get("knowledge") or [])
        ck("g2 拿不到 g1 的记忆", "Rust 项目" not in other_text, other_text[:60])
        ck("g2 能拿到 public 的 global", "长代码" in other_text, other_text[:60])

        print()
        print("== 去重：重复的 add 被跳过 ==")
        await mgr.emit("message.received", {"scope": {"kind": "group", "id": 1, "key": "g1"},
                                            "sender": {"id": 7, "name": "某人"},
                                            "text": "随便说说", "ts": 2000})
        await asyncio.sleep(0.3)
        REPLIES.append(json.dumps({"ops": [
            {"op": "add", "space": "session", "text": "本群某人在折腾 Rust 项目",
             "importance": 4}]}, ensure_ascii=False))
        await mgr.run_job("memory", "consolidate")
        sess2 = json.loads((td / "data" / "spaces" / "g1.json").read_text(encoding="utf-8"))
        dup = [e["text"] for e in sess2["entries"] if e["text"] == "本群某人在折腾 Rust 项目"]
        ck("重复条目没被写第二遍", len(dup) == 1, str(len(dup)))

        print()
        print("== 模型说没什么可记 → 不写 ==")
        await mgr.emit("message.received", {"scope": {"kind": "group", "id": 1, "key": "g1"},
                                            "sender": {"id": 7, "name": "某人"},
                                            "text": "哈哈", "ts": 3000})
        await asyncio.sleep(0.3)
        REPLIES.append('{"ops": []}')
        await mgr.run_job("memory", "consolidate")
        sess4 = json.loads((td / "data" / "spaces" / "g1.json").read_text(encoding="utf-8"))
        ck("空操作不写东西", len(sess4["entries"]) == len(sess2["entries"]),
           "%d → %d" % (len(sess2["entries"]), len(sess4["entries"])))

        await mgr.close()
    finally:
        srv.shutdown()
        shutil.rmtree(td, ignore_errors=True)

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
