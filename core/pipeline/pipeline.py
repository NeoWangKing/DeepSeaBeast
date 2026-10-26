"""把四个阶段串起来：allowlist → merge → rate_limit → decide。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import stages
from .config import GateConfig
from .state import ChatState, StateStore
from .types import Decision, MessageCtx, StageResult

log = logging.getLogger("pipeline")


@dataclass(slots=True)
class Outcome:
    """一次判断的最终结果。"""

    kind: str = "drop"          # reply / silence / drop / defer
    reason: str = ""
    text: str = ""              # defer 时合并后的文本
    decision: Decision = field(default_factory=Decision)
    stage: str = ""             # 在哪一段定下来的（排错用）
    hints: list[str] = field(default_factory=list)   # 场面提示（喂给模型，也进日志）

    def __bool__(self) -> bool:
        return self.kind == "reply"


def _skey(msg) -> str:
    """会话标识：优先用核心算的不透明引用，退化为通道原始 key。"""
    return str(getattr(msg.chat, "ref", "") or msg.chat.key)


class Pipeline:
    def __init__(self, cfg: GateConfig, store: StateStore | None = None) -> None:
        self.cfg = cfg
        self.store = store or StateStore()

    def state_for(self, key: str) -> ChatState:
        return self.store.get(key)

    def run(self, msg, now: float, *, skip: tuple[str, ...] = (),
            identity_names: list[str] | None = None) -> Outcome:
        """跑一遍管线。skip 用来在"重放合并文本"时跳过 merge 段。"""
        ctx = MessageCtx(msg=msg, now=now, text=msg.text,
                         identity_names=list(identity_names or []))
        state = self.state_for(_skey(msg))

        # 场面缓冲：每条消息都要记，否则"冷场/被晾/很吵"这些信号全是假的
        # （2026-10-27 发现：重构时漏了写入，cold_room 恒为 1、unanswered 恒为 0）
        state.scene.note_incoming(now, msg.sender.id, msg.text)
        if any(getattr(seg, "kind", "") == "poke" for seg in msg.message.segments):
            state.scene.note_poke(now, msg.sender.id)

        steps = [
            ("allowlist", lambda: stages.stage_allowlist(ctx, self.cfg)),
            ("policy", lambda: stages.stage_policy(ctx, state, self.cfg)),
            ("score", lambda: stages.stage_score(ctx, state, self.cfg)),
        ]
        for name, fn in steps:
            if name in skip:
                continue
            r = fn()
            if r.kind == "drop":
                log.debug("drop@%s: %s", name, r.reason)
                return Outcome(kind="drop", reason=r.reason, stage=name)

        d = stages.stage_decide(ctx, state, self.cfg)
        if not d:
            return Outcome(kind="silence", reason=d.reason, decision=d, stage="decide")
        if d.action == "silence":
            stages.note_silence(state, ctx)
            return Outcome(kind="silence", reason=d.reason, decision=d, stage="decide",
                           hints=list(ctx.hints))

        # 合并放在"决定要回"之后：只有真要回的连发才值得攒，
        # 否则被拦下的消息会污染缓冲，把下一条该回的也吞掉（2026-10-22 测出来的）
        if "merge" not in skip:
            m = stages.stage_merge(ctx, state, self.cfg)
            if m.kind == "defer":
                log.debug("defer@merge: %s", m.reason)
                return Outcome(kind="defer", reason=m.reason, text=m.text, stage="merge")
            if m.text and m.text.strip() != ctx.text.strip():
                return Outcome(kind="reply", reason=m.reason, text=m.text,
                               decision=d, stage="merge",
                               hints=list(ctx.hints))   # 用合并后的整体文本

        return Outcome(kind="reply", reason=d.reason, decision=d, stage="decide",
                       hints=list(ctx.hints))

    def note_replied(self, msg, now: float) -> None:
        """她真的发出去了之后调一次。"""
        ctx = MessageCtx(msg=msg, now=now, text=msg.text)
        stages.note_replied(self.state_for(_skey(msg)), ctx, self.cfg)
