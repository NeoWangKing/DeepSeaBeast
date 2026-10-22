"""发言节奏：让她"一条一条地说"，而不是一次甩出一堆。

为什么在核心里：这是"像人"的基本行为，和通道无关——QQ 气泡、终端行、
以后任何前端都应该同一条节奏。所以统一放在 ChannelManager 的出口处。

模型（从旧实现调出来的经验值）：
    下一条的等待 = 上一条字数 × sec_per_char + base_sec，封顶 max_sec
再叠一点随机抖动，避免机械感。

只在**同一次回复内部**生效：新一轮的第一条不等待（模型的思考时间已经够久了）。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass

log = logging.getLogger("pacing")


@dataclass(slots=True)
class PacingConfig:
    enabled: bool = True
    base_sec: float = 1.2          # 每条至少间隔
    sec_per_char: float = 0.035    # 按上一条长度加时间
    max_sec: float = 4.0           # 封顶
    jitter: float = 0.2            # ±20% 抖动

    @classmethod
    def from_dict(cls, d: dict) -> "PacingConfig":
        c = cls()
        c.enabled = bool(d.get("enabled", c.enabled))
        c.base_sec = float(d.get("base_sec", c.base_sec))
        c.sec_per_char = float(d.get("sec_per_char", c.sec_per_char))
        c.max_sec = float(d.get("max_sec", c.max_sec))
        c.jitter = float(d.get("jitter", c.jitter))
        return c


class Pacer:
    """记录每个会话"上一条发了什么、什么时候发的"，据此算下一条的间隔。"""

    def __init__(self, cfg: PacingConfig | None = None) -> None:
        self.cfg = cfg or PacingConfig()
        self._last: dict[str, tuple[int, float]] = {}   # chat_key → (字数, 时间戳)

    def reset(self, chat_key: str) -> None:
        """新的一轮开始：第一条不用等。"""
        self._last.pop(chat_key, None)

    def delay_for(self, chat_key: str) -> float:
        """这条要等多久（秒）。纯函数，方便测。"""
        if not self.cfg.enabled:
            return 0.0
        prev = self._last.get(chat_key)
        if prev is None:
            return 0.0
        chars, _ts = prev
        d = self.cfg.base_sec + chars * self.cfg.sec_per_char
        d = min(d, self.cfg.max_sec)
        if self.cfg.jitter > 0:
            d *= 1.0 + random.uniform(-self.cfg.jitter, self.cfg.jitter)
        return max(0.0, d)

    async def before_send(self, chat_key: str, text: str) -> float:
        """等到"该说话的时候"，然后记下这条。返回实际等待秒数。"""
        d = self.delay_for(chat_key)
        if d > 0:
            log.debug("节奏：%s 等下一条 %.2fs", chat_key, d)
            await asyncio.sleep(d)
        self._last[chat_key] = (len(text or ""), time.time())
        return d
