"""管线里流转的几种结果类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..channel import InboundMessage


@dataclass(slots=True)
class StageResult:
    """一个阶段跑完的结论。

    kind:
      · pass   继续往下走
      · drop   到此为止，不回，也不留状态
      · defer  先不回，等一会儿（连发合并）
    """

    kind: str = "pass"          # pass / drop / defer
    reason: str = ""
    text: str = ""              # defer 时：合并后的文本

    @property
    def ok(self) -> bool:
        return self.kind == "pass"


@dataclass(slots=True)
class Decision:
    """要不要回、回什么（M2 只有"回一句占位话"，M3 换成 LLM）。"""

    action: str = "silence"     # reply / wait / silence
    intent: str = ""
    reason: str = ""

    def __bool__(self) -> bool:
        return self.action == "reply"


@dataclass(slots=True)
class MessageCtx:
    """一份消息 + 判断它需要的上下文。"""

    msg: InboundMessage
    now: float
    text: str = ""
    identity_names: list[str] = field(default_factory=list)   # 这个会话里她叫什么
    hints: list[str] = field(default_factory=list)            # 场面提示（喂给模型）
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def chat_key(self) -> str:
        return str(getattr(self.msg.chat, "ref", "") or self.msg.chat.key)

    @property
    def sender_id(self) -> int:
        return self.msg.sender.id
