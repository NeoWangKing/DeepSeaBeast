"""作用域：她"在哪儿"说话。

三个层次（越往下越大）：
  local  —— 命令行（和主人一对一）
  private—— QQ 私聊
  group  —— QQ 群

作用域决定两件事：**记忆怎么分区**、**工具给哪些**。
"""

from __future__ import annotations

from dataclasses import dataclass

from .channel import ChatRef
from .kinds import is_personal


@dataclass(frozen=True, slots=True)
class Scope:
    kind: str          # local / private / group
    key: str           # cli / p<QQ> / g<群号>
    id: int = 0

    @staticmethod
    def from_chat(chat: ChatRef) -> "Scope":
        return Scope(kind=chat.kind, key=chat.key, id=chat.id)

    @property
    def is_personal(self) -> bool:
        """私人作用域（命令行 / 私聊主人）——可以用全部工具。"""
        return is_personal(self.kind)

    @property
    def dirname(self) -> str:
        """目录名：cli / p200000001 / g100000001。"""
        return self.key
