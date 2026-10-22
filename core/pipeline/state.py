"""每会话的运行时状态（频率、连发合并、最近消息）。

只放内存——进程重启就清空。需要跨重启的（群印象、人格）走插件/存储层。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass(slots=True)
class ChatState:
    """一个会话（群或私聊）的状态。"""

    last_reply_ts: float = 0.0          # 她上次开口的时间
    last_reply_to: int = 0              # 上次回的是谁
    last_msg_ts: float = 0.0            # 上一条群友消息时间
    last_sender: int = 0                # 上一条群友消息是谁
    hour_bucket: int = -1               # 当前小时桶
    hour_count: int = 0                 # 本小时回过几条
    # 连发合并：同一个人的连续短消息先攒着
    merge_buf: str = ""
    merge_ts: float = 0.0
    merge_sender: int = 0
    recent: deque[tuple[str, str, int, float]] = field(
        default_factory=lambda: deque(maxlen=20))   # (谁, 文本, uid, ts)

    def roll_hour(self, now: float) -> None:
        """跨小时就把计数清零。"""
        bucket = int(now // 3600)
        if bucket != self.hour_bucket:
            self.hour_bucket = bucket
            self.hour_count = 0


class StateStore:
    """按 chat_key 存 ChatState。"""

    def __init__(self) -> None:
        self._data: dict[str, ChatState] = {}

    def get(self, key: str) -> ChatState:
        st = self._data.get(key)
        if st is None:
            st = ChatState(hour_bucket=int(time.time() // 3600))
            self._data[key] = st
        return st

    def clear(self) -> None:
        self._data.clear()
