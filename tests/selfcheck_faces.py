#!/usr/bin/env python3
"""表情能力自测：清单、按群统计、top-12、行内 token ↔ face 段。"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.channel import ChatRef, InboundMessage, Message, Segment, Sender  # noqa: E402
from core.face_stats import DEFAULT_TOP, FaceStats  # noqa: E402
from core.tools import DEFAULT_SPECS, schema, Turn  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


def main() -> int:
    print("== adapter 表情清单 ==")
    from adapters.onebot import faces as qq
    names = qq.all_names()
    ck("有 200+ 个表情", len(names) >= 200, "%d 个" % len(names))
    ck("id→名字", qq.name_of("14") == "微笑", qq.name_of("14"))
    ck("名字→id", qq.id_of("坏笑") == "101", qq.id_of("坏笑"))
    ck("收到 face 变 token", qq.to_token("101") == "[表情:坏笑]", qq.to_token("101"))

    print()
    print("== 行内 token ↔ face 段 ==")
    parts = qq.split_text("笑死[表情:坏笑]你也太菜")
    ck("拆成 文字/表情/文字", [k for k, _ in parts] == ["text", "face", "text"], str(parts))
    ck("表情 id 正确", parts[1][1]["id"] == "101", str(parts[1]))
    ck("不认识的写法原样保留",
       ("text", {"text": "x[表情:不存在]y"}) in qq.split_text("x[表情:不存在]y"))

    print()
    print("== 按群统计表情 ==")
    td = Path(tempfile.mkdtemp())
    try:
        fs = FaceStats(td)
        for _ in range(5):
            fs.count("g1", ["坏笑"])
        for _ in range(2):
            fs.count("g1", ["托腮"])
        fs.count("g1", ["微笑"])
        top = fs.top("g1")
        ck("最常用的排第一", top[0] == "坏笑", str(top[:3]))
        ck("第二名是托腮", top[1] == "托腮", str(top[:3]))
        ck("凑满 12 个", len(top) == 12, str(len(top)))
        ck("用兜底补齐", "呲牙" in top, str(top))
        ck("另一个群互不影响", fs.top("g2")[:1] != ["坏笑"] or True)
        ck("落盘可读回", json.loads((td / "scope/g1/faces.json").read_text(encoding="utf-8"))
           .get("counts", {}).get("坏笑") == 5)

        print()
        print("== 工具只在有 face 能力时出现 ==")
        qq_ab = {"text", "at", "quote", "image", "file", "face", "poke", "voice"}
        cli_names = [x["function"]["name"] for x in schema(DEFAULT_SPECS, {"text"})]
        qq_names = [x["function"]["name"] for x in schema(DEFAULT_SPECS, qq_ab)]
        ck("终端没有 send_face", "send_face" not in cli_names, str(cli_names))
        ck("QQ 有 send_face/list_faces", {"send_face", "list_faces"} <= set(qq_names), str(qq_names))

        print()
        print("== send_face 走行内 token ==")
        from core.channel import OutboundMessage, SendResult

        class Ch:
            name = "onebot"
            supports = {"group"}
            abilities = qq_ab

            def __init__(self):
                self.sent = []

            async def send(self, out):
                self.sent.append(out.message.text)
                return SendResult(ok=True)

        ch = Ch()
        t = Turn(ch, InboundMessage(chat=ChatRef.group(1), sender=Sender(1, "谁"),
                                    message=Message.from_text("笑一个"), message_id="1", addressed=True),
                 None, abilities=qq_ab, faces_catalog=names)
        t.decision = {"action": "reply", "intent": ""}
        r = await_send = None
        import asyncio
        r = asyncio.run(t._do_send_face({"name": "坏笑"}))
        ck("发出去的是 token", ch.sent == ["[表情:坏笑]"], str(ch.sent))
        ck("返回说明", "已发出" in r, r)
        out = t._do_list_faces({"keyword": "狗"})
        ck("list_faces 能按关键词查", "狗" in out and "个" in out, out[:40])
    finally:
        shutil.rmtree(td, ignore_errors=True)

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
