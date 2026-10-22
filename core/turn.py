"""回合（turn）：一次"她听到消息后说话"的完整过程。

为什么要显式建模：
  · 说话要能被**打断**（群友补一句、换话题），否则她会答非所问
  · 花钱要有**闸**（token 预算、墙钟上限），否则死循环能烧穿
  · 排查要有**线索**（turn id），一条消息 → N 次模型调用 → M 条气泡，能串起来

取消是**协作式**的：只置标记，由 agent/tools 在检查点停下。
这样不会留下"取消到一半"的烂状态（发出去的气泡也收不回，但不会再补发）。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from .channel import ChatRef


def new_turn_id() -> str:
    return "t-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:4])


@dataclass(slots=True)
class TurnConfig:
    max_tokens: int = 20000        # 单回合 token 上限（含所有轮次）
    max_seconds: float = 45.0      # 单回合墙钟上限
    max_rounds: int = 6            # 模型-工具循环上限
    max_sends: int = 4             # 最多几条气泡
    wait_recheck_sec: float = 5.0  # 她说"先等等"时，多久后再决定一次

    @classmethod
    def from_dict(cls, d: dict) -> "TurnConfig":
        c = cls()
        c.max_tokens = int(d.get("max_tokens", c.max_tokens))
        c.max_seconds = float(d.get("max_seconds", c.max_seconds))
        c.max_rounds = int(d.get("max_rounds", c.max_rounds))
        c.max_sends = int(d.get("max_sends", c.max_sends))
        c.wait_recheck_sec = float(d.get("wait_recheck_sec", c.wait_recheck_sec))
        return c


@dataclass(slots=True)
class TurnContext:
    """一次回合的身份证 + 预算 + 取消标记。"""

    id: str
    scope_key: str
    cfg: TurnConfig
    started_at: float = field(default_factory=time.time)
    tokens_used: int = 0
    rounds: int = 0
    sends: int = 0
    cancelled: bool = False
    cancel_reason: str = ""
    stop_reason: str = ""          # 正常结束的原因（finish / 说完 / 预算…）
    finished: bool = False         # 回合是否已经收尾
    chat: ChatRef | None = None

    # ── 取消 ──
    def cancel(self, reason: str) -> None:
        if not self.cancelled:
            self.cancelled = True
            self.cancel_reason = reason

    # ── 预算 ──
    @property
    def elapsed(self) -> float:
        return time.time() - self.started_at

    @property
    def expired(self) -> bool:
        return self.elapsed > self.cfg.max_seconds

    @property
    def over_budget(self) -> bool:
        return self.tokens_used > self.cfg.max_tokens

    def add_usage(self, usage: dict | None) -> None:
        u = usage or {}
        try:
            self.tokens_used += int(u.get("total_tokens")
                                    or (int(u.get("prompt_tokens") or 0)
                                        + int(u.get("completion_tokens") or 0)))
        except Exception:
            pass

    # ── 检查点 ──
    def should_stop(self) -> str:
        """返回非空字符串表示"该停了"，内容是原因。"""
        if self.cancelled:
            return "cancelled:%s" % (self.cancel_reason or "被打断")
        if self.expired:
            return "timeout:%.0fs" % self.elapsed
        if self.over_budget:
            return "budget:%d/%d" % (self.tokens_used, self.cfg.max_tokens)
        return ""

    def describe(self) -> str:
        return ("%s rounds=%d sends=%d tokens=%d elapsed=%.1fs%s"
                % (self.id, self.rounds, self.sends, self.tokens_used, self.elapsed,
                   (" stop=" + self.stop_reason) if self.stop_reason else ""))
