"""每会话的运行时状态（门控用：发言额度、对话状态、最近场面）。

只放内存——进程重启就清空。需要跨重启的（群印象、人格）走插件/存储层。
`memory=off` 的会话**连这里的状态都不该持久化**（我们不写盘，所以天然满足）。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from ..signals import Scene


@dataclass(slots=True)
class ChatState:
    """一个会话（群或私聊）的状态。"""

    # ── 发言节奏 ──
    last_reply_ts: float = 0.0          # 她上次开口的时间
    last_reply_to: int = 0              # 上次回的是谁
    credit: float = 0.0                 # 发言额度（像"还有几次开口机会"）
    credit_ts: float = 0.0              # 上次结算额度的时间
    # ── 连续对话 ──
    dialogue_peer: int = 0              # 正在跟谁对话
    dialogue_until: float = 0.0         # 对话窗口到什么时候
    dialogue_turns: int = 0             # 这一轮已经聊了几个来回
    dialogue_silent: int = 0            # 她在这轮里连续沉默了几次
    # ── 预算 ──
    hour_bucket: int = -1
    hour_count: int = 0                 # 本小时回过几条
    decide_bucket: int = -1
    decide_count: int = 0               # 本小时进过几次 decide（成本）
    # ── 连发合并 ──
    merge_buf: str = ""
    merge_ts: float = 0.0
    merge_first_ts: float = 0.0
    merge_sender: int = 0
    # ── 场面 ──
    scene: Scene = field(default_factory=Scene)
    last_msg_ts: float = 0.0
    last_sender: int = 0

    def roll_hour(self, now: float) -> None:
        bucket = int(now // 3600)
        if bucket != self.hour_bucket:
            self.hour_bucket = bucket
            self.hour_count = 0
        if bucket != self.decide_bucket:
            self.decide_bucket = bucket
            self.decide_count = 0

    def refill(self, now: float, credit_max: float, refill_sec: float) -> None:
        """按时间回充发言额度（对话中回得更快，由调用方传更小的 refill_sec）。"""
        if self.credit_ts <= 0:
            self.credit = credit_max
            self.credit_ts = now
            return
        if refill_sec <= 0:
            return
        gained = (now - self.credit_ts) / refill_sec
        if gained > 0:
            self.credit = min(credit_max, self.credit + gained)
            self.credit_ts = now


class StateStore:
    """按 chat_key 存 ChatState。"""

    def __init__(self) -> None:
        self._data: dict[str, ChatState] = {}

    def get(self, key: str) -> ChatState:
        st = self._data.get(key)
        if st is None:
            st = ChatState(hour_bucket=int(time.time() // 3600),
                           decide_bucket=int(time.time() // 3600))
            self._data[key] = st
        return st

    def clear(self) -> None:
        self._data.clear()
