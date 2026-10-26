"""门控各阶段：白名单 → 策略过滤 → 打分 → decide → 记录。

设计（2026-10-26 重构）：
  · 每个会话一份 ChatPolicy（core/policy.py），档位只管"进不进 decide"
  · 没被叫到时用**发言额度 + 场面打分**决定要不要问模型（core/signals.py）
  · 连续对话是一个状态机（dialogue_peer / dialogue_until / dialogue_turns）
  · 回不回始终由 decide（模型）判断——这里只拦"不值得问"的消息，不替她决定说什么
"""

from __future__ import annotations

import logging
import random
import time

from .. import signals as sig_mod
from ..policy import resolve
from ..typing import looks_unfinished
from .config import GateConfig
from .state import ChatState
from .types import Decision, MessageCtx, StageResult

log = logging.getLogger("gate")


def policy_of(ctx: MessageCtx, cfg: GateConfig):
    """这个会话的门控策略（每次现算，配置改了立即生效）。"""
    return resolve(cfg.default_preset, cfg.chats, ctx.msg.chat.kind, ctx.msg.chat.id)


# ── ① 白名单 ──────────────────────────────────────────
def stage_allowlist(ctx: MessageCtx, cfg: GateConfig) -> StageResult:
    """白名单 / 私聊权限：不在管辖范围就彻底不管。"""
    msg = ctx.msg
    kind = msg.chat.kind
    if kind == "local":
        return StageResult("pass", "本地模式")
    if kind == "private":
        if cfg.private_owner_only and not msg.sender.is_owner:
            return StageResult("drop", "私聊只理主人")
        return StageResult("pass", "私聊")
    if kind == "room":
        return StageResult("pass", "房间")
    if cfg.allowed_groups and msg.chat.id not in cfg.allowed_groups:
        return StageResult("drop", "群不在白名单")
    return StageResult("pass", "群在白名单")


# ── ② 连发合并 ────────────────────────────────────────
def stage_merge(ctx: MessageCtx, state: ChatState, cfg: GateConfig) -> StageResult:
    """连发合并 / 等她说完整。"""
    msg = ctx.msg
    text = ctx.text
    same = state.merge_sender == msg.sender.id
    # 只要她还没在这之后开口，就继续攒着——超时但没回话也不能把上一句丢了
    # （踩过：问句被判"没说完"挂起重放，8 秒后新消息进来把它整段顶掉）
    no_reply_since = state.last_reply_ts <= (state.merge_first_ts or 0)
    fresh = bool(state.merge_buf) and same and (
        (ctx.now - state.merge_ts) <= cfg.typing_wait_sec or no_reply_since)

    if fresh:
        state.merge_buf = (state.merge_buf + " " + text).strip()[:400]
        state.merge_ts = ctx.now
        merged = state.merge_buf
        waited = ctx.now - state.merge_first_ts
        if looks_unfinished(text) and waited < cfg.typing_max_wait_sec:
            return StageResult("defer", "她还在说，再等 %0.0fs" % cfg.typing_wait_sec, merged)
        return StageResult("pass", "合并后一起回", merged)

    state.merge_buf = text
    state.merge_ts = ctx.now
    state.merge_first_ts = ctx.now
    state.merge_sender = msg.sender.id
    if looks_unfinished(text):
        return StageResult("defer", "像是没说完，等一下", text)
    return StageResult("pass", "说完了")


# ── ③ 连续对话判断 ────────────────────────────────────
def in_dialogue(state: ChatState, ctx: MessageCtx) -> bool:
    """她刚跟这个说话人聊过，且还在窗口内。"""
    return bool(state.dialogue_until and ctx.now <= state.dialogue_until
                and state.dialogue_peer == ctx.msg.sender.id)


def dialogue_expired(state: ChatState, ctx: MessageCtx) -> bool:
    return bool(state.dialogue_until) and ctx.now > state.dialogue_until


# ── ④ 策略过滤：静默时段 / 只认被叫 / 硬上限 ────────────
def stage_policy(ctx: MessageCtx, state: ChatState, cfg: GateConfig) -> StageResult:
    msg = ctx.msg
    pol = policy_of(ctx, cfg)

    # 静默时段
    if pol.quiet_now(time.strftime("%H:%M", time.localtime(ctx.now))):
        return StageResult("drop", "静默时段")

    # strict：只认被叫到（其余连 decide 都不进，也不落任何记录）
    if pol.mode == "strict" and not msg.addressed:
        return StageResult("drop", "严格模式：只理被叫到的消息")

    state.roll_hour(ctx.now)
    if state.decide_count >= cfg.max_decide_per_hour:
        return StageResult("drop", "本小时 decide 次数用完（%d）" % cfg.max_decide_per_hour)
    if state.hour_count >= cfg.max_auto_per_hour:
        return StageResult("drop", "本小时回复额度用完（%d）" % cfg.max_auto_per_hour)
    return StageResult("pass", "策略通过")


# ── ⑤ 打分：要不要问她 ────────────────────────────────
def stage_score(ctx: MessageCtx, state: ChatState, cfg: GateConfig) -> StageResult:
    """没被叫到时，用额度 + 场面打分决定要不要进 decide。"""
    msg = ctx.msg
    pol = policy_of(ctx, cfg)

    # 对话进行中：直接用
    if pol.continuation and in_dialogue(state, ctx):
        return StageResult("pass", "对话进行中")

    # 补满额度（对话中回充更快）
    refill_sec = cfg.credit_refill_min * 60.0
    if pol.continuation and state.dialogue_until:
        refill_sec = max(30.0, refill_sec / max(1.0, cfg.dialogue_refill_boost))
    state.refill(ctx.now, cfg.credit_max, refill_sec)

    names = list(getattr(ctx, "identity_names", []) or [])
    s, hints = sig_mod.compute(state.scene, msg, names,
                              dialogue_active=in_dialogue(state, ctx),
                              budget_ratio=state.decide_count / max(1, cfg.max_decide_per_hour),
                              now=ctx.now)
    ctx.hints = hints

    # loose：不做代码层拦截，每条都交给模型；只把"该少说点"的信号喂给它
    if pol.mode == "loose":
        return StageResult("pass", "宽松：每条都问模型")

    # 被叫到：一定进（额度不足也让它进，人不会因为"配额"装没听见）
    if msg.addressed:
        return StageResult("pass", "被叫到")

    # 一对一和本地：本来就是冲她来的，不需要打分
    if msg.chat.kind in ("local", "private"):
        return StageResult("pass", "一对一")

    if state.credit < 1.0:
        return StageResult("drop", "发言额度不足（%.1f）" % state.credit)

    total = sig_mod.score(s, pol.weights)
    threshold = cfg.score_threshold + random.uniform(-1.0, 1.0)
    ctx.hints = hints
    if total >= threshold:
        return StageResult("pass", "打分通过 %.1f≥%.1f" % (total, threshold))
    return StageResult("drop", "打分没过 %.1f<%.1f" % (total, threshold))


# ── ⑥ 由模型决定 ──────────────────────────────────────
def stage_decide(ctx: MessageCtx, state: ChatState, cfg: GateConfig) -> Decision:
    """最终决定权在模型；这里只记录"为什么问它"。"""
    msg = ctx.msg
    state.decide_count += 1
    if msg.addressed:
        return Decision("reply", "被叫到", msg.addressed_by or "被点名")
    if in_dialogue(state, ctx):
        return Decision("reply", "对话进行中", "刚接过话，继续说")
    return Decision("reply", "", "场面值得接一句")


# ── ⑦ 她说完了 ────────────────────────────────────────
def note_replied(state: ChatState, ctx: MessageCtx, cfg: GateConfig | None = None) -> None:
    pol = policy_of(ctx, cfg) if cfg else None
    state.last_reply_ts = ctx.now
    state.last_reply_to = ctx.msg.sender.id
    state.hour_count += 1
    state.scene.note_replied(ctx.now)
    state.scene.last_speak_ts = ctx.now

    # 额度：对话中少扣一点（连续聊不该那么贵）
    cost = 0.5 if (pol and pol.continuation and in_dialogue(state, ctx)) else 1.0
    state.credit = max(0.0, state.credit - cost)

    # 连续对话
    if pol is not None and pol.continuation:
        if in_dialogue(state, ctx):
            state.dialogue_turns += 1
        else:
            state.dialogue_peer = ctx.msg.sender.id
            state.dialogue_turns = 1
        state.dialogue_until = ctx.now + cfg.dialogue_window_sec if cfg else ctx.now
        state.dialogue_silent = 0

    # 连发合并收尾
    state.merge_buf = ""
    state.merge_ts = 0.0
    state.merge_first_ts = 0.0
    state.merge_sender = 0


def note_silence(state: ChatState, ctx: MessageCtx) -> None:
    """她决定不说话：对话里连续沉默两次就结束这段对话（话题聊不下去）。"""
    if not in_dialogue(state, ctx):
        return
    state.dialogue_silent += 1
    if state.dialogue_silent >= 2:
        state.dialogue_until = 0.0
        state.dialogue_peer = 0
        state.dialogue_turns = 0
        state.dialogue_silent = 0
