#!/usr/bin/env python3
"""假的 OneBot 客户端：扮演 SnowLuma，连到核心发一条群消息并等它调 send_group_msg。

用法：
  .venv/bin/python tests/fake_onebot.py [ws://127.0.0.1:6200]
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets

ARMED = True


async def main(url: str = "ws://127.0.0.1:6200") -> None:
    async with websockets.connect(url) as ws:
        # 1) 先发一条 lifecycle，模拟 SnowLuma 上线
        await ws.send(json.dumps({"post_type": "meta_event", "meta_event_type": "lifecycle",
                                  "sub_type": "connect", "self_id": 3237702352}))
        # 2) 再发一条"被 @ 的群消息"
        await ws.send(json.dumps({
            "post_type": "message", "message_type": "group",
            "self_id": 3237702352, "user_id": 3245938285, "group_id": 1109525246,
            "message_id": 10001, "time": 1792239000,
            "sender": {"card": "灵其啊", "nickname": "灵其啊"},
            "message": [{"type": "at", "data": {"qq": "3237702352"}},
                        {"type": "text", "data": {"text": " 在吗"}}],
            "raw_message": "[At:3237702352] 在吗",
        }, ensure_ascii=False))
        print("[fake] 已发送 2 条上报，等核心的 action 调用…")

        # 3) 如果核心调动作，回一个成功回包（M1 会用到）
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                data = json.loads(raw)
                if "echo" in data:
                    print("[fake] 收到 action:", data.get("action"), data.get("params"))
                    await ws.send(json.dumps({"status": "ok", "retcode": 0,
                                              "data": {"message_id": 20001},
                                              "echo": data["echo"]}))
        except asyncio.TimeoutError:
            print("[fake] 5 秒内没有 action 调用（M0 阶段正常）")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:6200"))
