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
    key: str                      # 通道自己的原始 key（如 g<群id>）；**核心不拿它做存储**
    ref: str = ""                 # 核心算的不透明引用（scope_ref）；存储只认它

    @staticmethod
    def group(gid: int) -> ChatRef:
        return ChatRef("group", int(gid), f"g{int(gid)}")

    @staticmethod
    def private(uid: int) -> ChatRef:
        return ChatRef("private", int(uid), f"p{int(uid)}")

    @staticmethod
    def local() -> ChatRef:
        return ChatRef("local", 0, "cli")

    @staticmethod
    def room(rid: int, key: str) -> ChatRef:
        """多人在场、但没有平台 @ 机制的"房间"（自建聊天室这类）。"""
        return ChatRef("room", int(rid), str(key or ("r%d" % int(rid))))


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
    # 这条消息是不是冲她来的——**由通道判断**（QQ 的 @、微信的引用、
    # 聊天室里叫名字、终端里说话都算），核心只读结论，不认识"@"是什么
    addressed: bool = False
    addressed_by: str = ""        # at / name / quote / always / none / poke（排查用）
    # 这条消息"关于谁"——比如某人戳了某人，被戳的那个就是焦点。
    # 通道负责填，核心只当"有个对象"用（决定要不要跟着戳同一个）
    focus: Sender | None = None
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
    """一次发送的结果。

    `confirmed` 说的是**这个结论确定不确定**，不是"成功没成功"：

    | ok | confirmed | 含义 |
    |---|---|---|
    | True  | True  | 真的确认到了（回读/回执核对通过） |
    | True  | False | 接口收下了，但没法确认（通道不支持回读、文件上传没回执） |
    | False | True  | 明确失败，可以放心重试 |

    为什么要分开：`ok=True, confirmed=False` 的消息**重试可能造成重复**，
    而 `ok=False` 的可以安全重试。核心的重试策略靠这个区分。
    """

    ok: bool
    message_id: str = ""
    error: str = ""
    confirmed: bool = True
    # 这次失败"再试一次有没有意义"：通道最清楚（连接断了=可重试；参数错=别试了）
    retryable: bool = True


OnMessage = Callable[[InboundMessage], Awaitable[None]]


class Channel(Protocol):
    name: str
    abilities: set[str]      # 能做哪些动作：text / at / image / file / face / poke …
    supports: set[str]       # 负责哪类会话：local / group / private

    # 可选：这个媒介的"说话习惯"与"行内可用物"
    #   {"style": "...", "inline": {"faces": [名字...], "mention": bool, "markdown": bool},
    #    "limits": {"max_chars": 4500}}

    async def start(self, on_message: OnMessage) -> None: ...
    async def send(self, out: OutboundMessage) -> SendResult: ...
    async def stop(self) -> None: ...
