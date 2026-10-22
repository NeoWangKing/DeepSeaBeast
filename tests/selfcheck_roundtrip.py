#!/usr/bin/env python3
"""一体自测：同一进程里拉起核心(OneBot 通道) + 假 SnowLuma，验证"收→回"往返。

用法：.venv/bin/python tests/selfcheck_roundtrip.py
退出码：0 通过 / 1 失败
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import websockets  # noqa: E402

from adapters.onebot import OneBotChannel  # noqa: E402
from core.runtime import Runtime  # noqa: E402

PORT = 6299
BOT = 3237702352
OWNER = 3245938285


def step(msg: str) -> None:
    print("[selfcheck] %s" % msg, flush=True)


async def main() -> int:
    channel = OneBotChannel(host="127.0.0.1", port=PORT, allow_self_ids=[BOT],
                            owner_ids=[OWNER], bot_id=BOT)
    runtime = Runtime(channel, [OWNER])
    ch_task = asyncio.create_task(channel.start(runtime.on_message))
    await asyncio.sleep(0.5)
    step("核心已启动，端口 %d" % PORT)

    got: list[dict] = []
    try:
        async with websockets.connect(f"ws://127.0.0.1:{PORT}",
                                      open_timeout=5, close_timeout=2) as ws:
            step("假 SnowLuma 已连上")
            await ws.send(json.dumps({
                "post_type": "message", "message_type": "group",
                "self_id": BOT, "user_id": OWNER, "group_id": 1109525246,
                "message_id": 10001, "sender": {"card": "灵其啊"},
                "message": [{"type": "at", "data": {"qq": str(BOT)}},
                            {"type": "text", "data": {"text": " 在吗"}}],
            }, ensure_ascii=False))
            step("已发送上报，等核心的 action…")
            raw = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            got.append(raw)
            step("收到 action: %s" % raw.get("action"))
            if "echo" in raw:
                await ws.send(json.dumps({"status": "ok", "retcode": 0,
                                          "data": {"message_id": 20001},
                                          "echo": raw["echo"]}))
    except Exception as e:
        step("连接阶段出错：%r" % (e,))
        traceback.print_exc()
    finally:
        try:
            await asyncio.wait_for(channel.stop(), timeout=5)
        except BaseException:          # 包括 CancelledError：收工时被打断不算错
            pass
        ch_task.cancel()
        try:
            await ch_task
        except BaseException:
            pass

    if not got:
        print("FAILED：核心没有任何 action 调用")
        return 1
    act = got[0].get("action")
    segs = (got[0].get("params", {}).get("message") or [])
    has_reply = any(s.get("type") == "reply" for s in segs)
    has_text = any(s.get("type") == "text" and s.get("data", {}).get("text") for s in segs)
    print("结果：action=%s reply=%s text=%s" % (act, has_reply, has_text))
    if act != "send_group_msg" or not has_text:
        print("FAILED")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
