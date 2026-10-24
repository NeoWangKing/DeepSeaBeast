#!/usr/bin/env python3
"""TeamSpeak 通道自测：转义、事件解析、自己发的回显要忽略。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.teamspeak.channel import TeamSpeakChannel, escape, parse_line, unescape  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


async def main() -> int:
    print("== 转义 / 还原（TS 查询用 \\s 表示空格）==")
    ck("空格 → \\s", escape("a b") == "a\\sb", escape("a b"))
    ck("竖线 → \\p", escape("a|b") == "a\\pb", escape("a|b"))
    ck("斜杠 → \\/", escape("a/b") == "a\\/b", escape("a/b"))
    ck("往返一致", unescape(escape("你好 世界|a/b \\ x")) == "你好 世界|a/b \\ x",
       unescape(escape("你好 世界|a/b \\ x")))

    print()
    print("== 事件行解析 ==")
    line = r"notifytextmessage targetmode=2 msg=大肥鱼\s在吗 invokerid=9 invokername=NeoWangKing uid=abc="
    d = parse_line(line)
    ck("msg 还原出空格", d.get("msg") == "大肥鱼 在吗", str(d.get("msg")))
    ck("拿到发送者昵称", d.get("invokername") == "NeoWangKing", str(d.get("invokername")))
    ck("拿到 invokerid", d.get("invokerid") == "9", str(d.get("invokerid")))

    print()
    print("== 收到消息 → 核心消息（含「被叫到」判断）==")
    ch = TeamSpeakChannel(nickname="大肥鱼")
    ch._self_id = "9"
    got: list = []

    async def on_message(m):
        got.append(m)

    await ch._on_text(r"notifytextmessage targetmode=2 msg=大肥鱼\s在吗 invokerid=12 invokername=NeoWangKing",
                      on_message)
    await asyncio.sleep(0.05)      # _on_text 现在是派发任务，给事件循环一拍
    ck("收到一条", len(got) == 1, str(len(got)))
    if got:
        m = got[0]
        ck("文本正确", m.text == "大肥鱼 在吗", m.text)
        ck("发送者正确", m.sender.name == "NeoWangKing", m.sender.name)
        ck("叫了名字 → addressed", m.addressed is True, str(m.addressed))
        ck("会话是 room", m.chat.kind == "room", m.chat.kind)
        ck("会话 key 带频道号", "ts:" in m.chat.key, m.chat.key)

    print()
    print("== 自己发的回显要忽略 ==")
    got.clear()
    await ch._on_text(r"notifytextmessage targetmode=2 msg=我说的话 invokerid=9 invokername=大肥鱼",
                      on_message)
    await asyncio.sleep(0.05)
    ck("自己的消息没进核心", len(got) == 0, str(len(got)))

    print()
    print("== 没叫名字 → 不算 addressed（房间规则交给模型）==")
    got.clear()
    await ch._on_text(r"notifytextmessage targetmode=2 msg=今天天气不错 invokerid=12 invokername=X",
                      on_message)
    await asyncio.sleep(0.05)
    ck("addressed=False", got and got[0].addressed is False, str(got[0].addressed if got else None))

    print()
    print("== 能力：只有文字 ==")
    ck("abilities 只有 text", ch.abilities == {"text"}, str(ch.abilities))
    ck("profile 说明没有图片", "没有图片" in ch.profile()["style"], ch.profile()["style"][:26])

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
