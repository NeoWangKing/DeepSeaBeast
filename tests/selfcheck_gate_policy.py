#!/usr/bin/env python3
"""门控策略在运行时的效果：memory=off 不留痕、history 开关、tools 开关、场面提示。"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.channel import ChatRef, InboundMessage, Message, SendResult, Sender  # noqa: E402
from core.pipeline import GateConfig  # noqa: E402
from core.runtime import Runtime  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


class Ch:
    name = "onebot"
    supports = {"group", "private"}
    abilities = {"text", "at"}

    def __init__(self):
        self.sent = []

    async def send(self, out):
        self.sent.append(out.message.text)
        return SendResult(ok=True)

    async def start(self, on):
        pass

    async def stop(self):
        pass


async def main() -> int:
    td = tempfile.mkdtemp()
    ch = Ch()

    async def reply(msg):
        return "答:%s" % msg.text

    gate = GateConfig.from_dict({
        "default_preset": "strict",
        "chats": {
            "group:966812151": {"preset": "strict", "memory": "off",
                                "history": False, "continuation": False},
            "group:1": {"preset": "loose", "tools": "none"},
        },
    })
    rt = Runtime(ch, gate, [42], reply_fn=reply, data_dir=os.path.join(td, "rt"))

    print("== memory=off：一个字都不落盘 ==")
    m = InboundMessage(chat=ChatRef.group(966812151), sender=Sender(7, "豹友"),
                       message=Message.from_text("在吗。"), message_id="1",
                       addressed=True, addressed_by="at")
    await rt.on_message(m)
    rows = rt.recent.get(m.chat.ref)
    ck("没写 recent", rows == [], str(rows))
    recent_dir = os.path.join(td, "rt", "recent")
    files = os.listdir(recent_dir) if os.path.isdir(recent_dir) else []
    ck("recent 目录里没有这个会话的文件", len(files) == 0, str(files))
    # reply_fn 模式下不经过通道发送，看她有没有产出回复
    ck("但她还是回话了（@ 了）", rt._last_said.get(m.chat.ref) == "答:在吗。",
       repr(rt._last_said.get(m.chat.ref)))

    print()
    print("== 普通群照样留痕 ==")
    m2 = InboundMessage(chat=ChatRef.group(1), sender=Sender(8, "群友"),
                        message=Message.from_text("聊天。"), message_id="2",
                        addressed=True, addressed_by="at")
    await rt.on_message(m2)
    rows2 = rt.recent.get(m2.chat.ref)
    ck("普通群写了 recent", len(rows2) >= 1, str(rows2))

    print()
    print("== tools=none：只剩说话 ==")
    from core.tools import DEFAULT_SPECS, visible
    names = [s.name for s in visible(DEFAULT_SPECS, "none")]
    ck("tools=none 只留 decide/send_message/finish",
       set(names) == {"decide", "send_message", "finish"}, str(names))
    names2 = [s.name for s in visible(DEFAULT_SPECS, "chat_only")]
    ck("chat_only 还能发表情", "send_face" in names2, str(names2))
    ck("chat_only 不能发文件", "send_file" not in names2, str(names2))

    print()
    print("== 场面提示进了提示词 ==")
    rt._hints["s_x"] = ["上一条已经没人接 200 秒了"]
    from core.prompt import build_scene
    scene = build_scene(m2, where="QQ群", self_name="大肥鱼",
                        hints=rt._hints["s_x"])
    ck("场景里带【场面】", "场面：" in scene and "没人接" in scene, scene[-40:])

    print()
    print("== 反重复：她最近说过的话会进场景 ==")
    rt._remember_said("s_x", "这个称呼，还是别在这里叫")
    rt._remember_said("s_x", "这个称呼，还是别在这里叫")     # 同一条不重复记
    rt._remember_said("s_x", "在")
    said = rt._recent_said("s_x")
    ck("记住最近说过的话", said == ["这个称呼，还是别在这里叫", "在"], str(said))
    scene2 = build_scene(m2, where="QQ群", self_name="大肥鱼", said_recent=said)
    ck("场景里提醒别重复", "别重复" in scene2, scene2[-46:])
    ck("带了具体说过的话", "这个称呼" in scene2, scene2[-46:])

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
