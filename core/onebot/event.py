"""OneBot v11 的消息模型。

只做"够用"的那部分：把原始报文解析成组件列表 + 常用快捷方法。
刻意不引入第三方依赖——协议本身就是 JSON。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── 消息段 ──────────────────────────────────────────────────────────
@dataclass(slots=True)
class Segment:
    """一个消息段（OneBot message 数组里的一项）。"""

    type: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """取这段的文字表示（text 直接给，at 给 @名字，其他给占位）。"""
        t = self.type
        d = self.data
        if t == "text":
            return str(d.get("text") or "")
        if t == "at":
            qq = str(d.get("qq") or "")
            return f"@{d.get('name') or qq}"
        if t == "image":
            return "<图片>"
        if t == "record":
            return "<语音>"
        if t == "file":
            return f"[文件:{d.get('file') or d.get('name') or ''}]"
        if t == "face":
            return f"[表情:{d.get('id')}]"
        if t == "reply":
            return ""
        return f"<{t}>"


# 常用的几种段做成类型别名，方便 isinstance 判断
class Plain(Segment):
    def __init__(self, text: str) -> None:
        super().__init__("text", {"text": text})


class At(Segment):
    def __init__(self, qq: str | int, name: str = "") -> None:
        super().__init__("at", {"qq": str(qq), "name": name})


class Image(Segment):
    def __init__(self, file: str = "", url: str = "") -> None:
        super().__init__("image", {"file": file, "url": url})


class Record(Segment):
    def __init__(self, file: str = "") -> None:
        super().__init__("record", {"file": file})


class File(Segment):
    def __init__(self, file: str = "", file_id: str = "", size: int = 0) -> None:
        super().__init__("file", {"file": file, "file_id": file_id, "file_size": size})


class Face(Segment):
    def __init__(self, id: str | int) -> None:
        super().__init__("face", {"id": str(id)})


class Reply(Segment):
    def __init__(self, id: str | int) -> None:
        super().__init__("reply", {"id": str(id)})


_SEG_CLS = {"text": Plain, "at": At, "image": Image, "record": Record,
            "file": File, "face": Face, "reply": Reply}


# ── 消息 ────────────────────────────────────────────────────────────
@dataclass(slots=True)
class Message:
    """一条消息：若干段 + 原始数组。"""

    segments: list[Segment] = field(default_factory=list)
    raw: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Any) -> Message:
        """从 OneBot 的 message 字段建（支持数组格式；字符串格式按纯文本处理）。"""
        if isinstance(raw, str):
            return cls(segments=[Plain(raw)], raw=[{"type": "text", "data": {"text": raw}}])
        arr = [x for x in (raw or []) if isinstance(x, dict)]
        out: list[Segment] = []
        for item in arr:
            t = str(item.get("type") or "")
            d = dict(item.get("data") or {})
            cls_ = _SEG_CLS.get(t)
            if cls_ is Plain:
                out.append(Plain(str(d.get("text") or "")))
            elif cls_ is At:
                out.append(At(d.get("qq") or "", str(d.get("name") or "")))
            elif cls_ is Image:
                out.append(Image(str(d.get("file") or ""), str(d.get("url") or "")))
            elif cls_ is Record:
                out.append(Record(str(d.get("file") or "")))
            elif cls_ is File:
                out.append(File(str(d.get("file") or ""), str(d.get("file_id") or ""),
                                int(d.get("file_size") or 0)))
            elif cls_ is Face:
                out.append(Face(d.get("id") or ""))
            elif cls_ is Reply:
                out.append(Reply(d.get("id") or ""))
            else:
                out.append(Segment(t, d))
        return cls(segments=out, raw=arr)

    @property
    def text(self) -> str:
        """整条消息的文字形式（图片/语音/文件用占位符）。"""
        return "".join(s.text for s in self.segments).strip()

    def segments_of(self, kind: str) -> list[Segment]:
        return [s for s in self.segments if s.type == kind]

    def to_raw(self) -> list[dict[str, Any]]:
        return [{"type": s.type, "data": s.data} for s in self.segments]


# ── 事件 ────────────────────────────────────────────────────────────
@dataclass(slots=True)
class Event:
    """一条上报事件（现在只覆盖 message / notice 的常用字段）。"""

    post_type: str = ""
    message_type: str = ""           # group / private
    self_id: int = 0                 # 机器人 QQ
    user_id: int = 0                 # 发送者
    group_id: int = 0
    message_id: int = 0
    sender_name: str = ""
    message: Message = field(default_factory=Message)
    raw: dict[str, Any] = field(default_factory=dict)

    # ── 快捷访问（跟旧代码保持同名，迁移时少改调用点）──
    @property
    def message_str(self) -> str:
        return self.message.text

    def get_self_id(self) -> str:
        return str(self.self_id)

    def get_sender_id(self) -> str:
        return str(self.user_id)

    def get_group_id(self) -> str:
        return str(self.group_id)

    def get_sender_name(self) -> str:
        return self.sender_name

    def get_messages(self) -> list[Segment]:
        return self.message.segments

    def is_at(self, qq: str | int) -> bool:
        """这条消息里有没有真的 @ 某个人。"""
        want = str(qq)
        return any(s.type == "at" and str(s.data.get("qq")) == want for s in self.message.segments)

    @property
    def chat_key(self) -> str:
        """会话唯一键：群聊 g<群号>，私聊 p<QQ>。"""
        if self.message_type == "group":
            return f"g{self.group_id}"
        return f"p{self.user_id}"

    @classmethod
    def from_raw(cls, d: dict[str, Any]) -> Event:
        sender = d.get("sender") or {}
        post = str(d.get("post_type") or "")
        return cls(
            post_type=post,
            message_type=str(d.get("message_type") or ""),
            self_id=int(d.get("self_id") or 0),
            user_id=int(d.get("user_id") or 0),
            group_id=int(d.get("group_id") or 0),
            message_id=int(d.get("message_id") or 0),
            sender_name=str(sender.get("card") or sender.get("nickname") or ""),
            message=Message.from_raw(d.get("message")),
            raw=d,
        )
