"""Channel 抽象：**核心不认识 QQ**。

核心只处理 InboundMessage（收到什么），产出 OutboundMessage（要说什么）；
具体怎么收发由 adapter 决定：CLI、OneBot(QQ)、以后别的 IM 都是 adapter。

adapter 用 `abilities` 声明自己能干什么（at/quote/image/file/face/poke…），
核心据此过滤工具——所以在 CLI 里不会冒出"发表情包"这种做不到的工具。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


# ── 消息模型（与 QQ 无关的最小集合）─────────────────────────────────
@dataclass(slots=True)
class Segment:
    """消息的一个片段。

    **核心只读 `text`（已归一化的可见文本）**；`data` 是通道私有数据，
    核心绝不解读——QQ 的段格式、别家的 JSON 结构都只属于 adapter。
    """

    kind: str                     # text / image / file / face / voice / at / other
    text: str = ""                # 归一化后的可见文本（adapter 负责填）
    data: dict[str, Any] = field(default_factory=dict)   # 通道私有，核心不解读

    @property
    def type(self) -> str:        # 兼容旧字段名
        return self.kind


@dataclass(slots=True)
class Message:
    segments: list[Segment] = field(default_factory=list)
    raw: Any = None

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segments).strip()

    def of(self, kind: str) -> list[Segment]:
        return [s for s in self.segments if s.type == kind]

    @classmethod
    def from_text(cls, text: str) -> Message:
        return cls(segments=[Segment("text", text=text)])


# ── 会话 / 发送者 ───────────────────────────────────────────────────
@dataclass(slots=True)
class ChatRef:
    kind: str                     # "group" | "private" | "local"
    id: int
    key: str                      # 作用域目录名，如 g<群id> / p<用户id> / cli

    @staticmethod
    def group(gid: int) -> ChatRef:
        return ChatRef("group", int(gid), f"g{int(gid)}")

    @staticmethod
    def private(uid: int) -> ChatRef:
        return ChatRef("private", int(uid), f"p{int(uid)}")

    @staticmethod
    def local() -> ChatRef:
        return ChatRef("local", 0, "cli")


@dataclass(slots=True)
class Sender:
    id: int
    name: str = ""
    is_owner: bool = False


@dataclass(slots=True)
class InboundMessage:
    chat: ChatRef
    sender: Sender
    message: Message
    message_id: str = ""
    at_me: bool = False
    is_wake: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.message.text


@dataclass(slots=True)
class OutboundMessage:
    chat: ChatRef
    message: Message
    quote_id: str = ""
    at_user: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SendResult:
    ok: bool
    message_id: str = ""
    error: str = ""


OnMessage = Callable[[InboundMessage], Awaitable[None]]


class Channel(Protocol):
    name: str
    abilities: set[str]      # 能做哪些动作：text / at / image / file / face / poke …
    supports: set[str]       # 负责哪类会话：local / group / private

    async def start(self, on_message: OnMessage) -> None: ...
    async def send(self, out: OutboundMessage) -> SendResult: ...
    async def stop(self) -> None: ...
