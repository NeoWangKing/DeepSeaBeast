#!/usr/bin/env python3
"""发送回执核对：retcode=0 不算成功，回读得到这条消息才算。"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.onebot.channel import OneBotChannel  # noqa: E402
from core.channel import ChatRef, Message, OutboundMessage  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def fake_client(port: int, get_msg_reply: dict, sent: list) -> None:
    """扮演 SnowLuma：回 send_group_msg 成功，get_msg 按剧本回。"""
    for _ in range(50):
        try:
            ws = await websockets.connect("ws://127.0.0.1:%d" % port)
            break
        except Exception:
            await asyncio.sleep(0.1)
    else:
        return
    async with ws:
        await ws.send(json.dumps({"post_type": "meta_event", "meta_event_type": "lifecycle",
                                  "sub_type": "connect", "self_id": 1}))
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=6)
                data = json.loads(raw)
                action, echo = data.get("action"), data.get("echo")
                if not echo:
                    continue
                sent.append(action)
                if action in ("send_group_msg", "send_private_msg"):
                    reply = {"status": "ok", "retcode": 0, "data": {"message_id": 20001}}
                elif action == "get_msg":
                    reply = get_msg_reply
                else:
                    reply = {"status": "ok", "retcode": 0, "data": {}}
                reply["echo"] = echo
                await ws.send(json.dumps(reply))
        except asyncio.TimeoutError:
            pass


async def one_case(name: str, get_msg_reply: dict, expect_ok: bool,
                   expect_confirmed: bool = True) -> list:
    port = free_port()
    ch = OneBotChannel(host="127.0.0.1", port=port, allow_self_ids=[1], bot_id=1,
                       verify_send=True, verify_timeout_ms=800)
    stop_task = asyncio.create_task(ch.start(lambda m: asyncio.sleep(0)))
    sent: list = []
    client = asyncio.create_task(fake_client(port, get_msg_reply, sent))
    await asyncio.sleep(0.8)
    res = await ch.send(OutboundMessage(chat=ChatRef.group(1),
                                        message=Message.from_text("测试一下")))
    print("  [%s] ok=%s confirmed=%s error=%r actions=%s"
          % (name, res.ok, res.confirmed, res.error, sent))
    ck("%s → 发送结果 %s" % (name, "成功" if expect_ok else "失败"), res.ok is expect_ok,
       str(res.error)[:50])
    ck("%s → 结论确定性 %s" % (name, expect_confirmed), res.confirmed is expect_confirmed,
       str(res.confirmed))
    if not expect_ok:
        ck("%s → 错误信息说清原因" % name, bool(res.error), str(res.error)[:40])
    client.cancel()
    await ch.stop()
    stop_task.cancel()
    await asyncio.sleep(0.2)
    return sent


async def main() -> int:
    print("== 回读查不到 → 判定失败（不能只看 retcode=0）==")
    sent = await one_case("消息没落地",
                          {"status": "ok", "retcode": 100, "wording": "message not found"},
                          expect_ok=False)
    ck("确实回读了（调了 get_msg）", "get_msg" in sent, str(sent))

    print()
    print("== 回读拿得到 → 判定成功 ==")
    await one_case("消息真的在",
                   {"status": "ok", "retcode": 0,
                    "data": {"message_id": 20001, "group_id": 1, "message": "x"}},
                   expect_ok=True)

    print()
    print("== 对方不支持 get_msg → 关闭核对，不误报 ==")
    sent = await one_case("不支持回读",
                          {"status": "failed", "retcode": 1404, "wording": "unknown action"},
                          expect_ok=True, expect_confirmed=False)
    ck("只尝试过回读", "get_msg" in sent, str(sent))

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
