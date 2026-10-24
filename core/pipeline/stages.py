"""四个门控阶段。

约定：每个函数都**不**修改全局状态以外的副作用；返回 StageResult。
顺序见 pipeline.py —— 改动顺序会改变行为，慎动。
"""

from __future__ import annotations

import random

from ..typing import looks_unfinished
from .config import GateConfig
from .state import ChatState
from .types import Decision, MessageCtx, StageResult


def stage_allowlist(ctx: MessageCtx, cfg: GateConfig) -> StageResult:
    """白名单 / 私聊权限：不在管辖范围就彻底不管。"""
    msg = ctx.msg
    if msg.chat.kind == "local":
        return StageResult("pass", "本地模式")
    if msg.chat.kind == "private":
        if cfg.private_owner_only and not msg.sender.is_owner:
            return StageResult("drop", "私聊只理主人")
        return StageResult("pass", "私聊")
    if msg.chat.kind == "room":
        # 房间没有"群白名单"的概念：连进哪个房间就是跟谁说话（由通道配置决定）
        return StageResult("pass", "房间")
    if cfg.allowed_groups and msg.chat.id not in cfg.allowed_groups:
        return StageResult("drop", "群不在白名单")
    return StageResult("pass", "群在白名单")


def stage_merge(ctx: MessageCtx, state: ChatState, cfg: GateConfig) -> StageResult:
    """连发合并 / 等她说完整。

    三种情况：
      ① 话没说完（"我跟你"）      → defer 等着，超时再正常回
      ② 上一串还没回，她又补一句   → 和前文合并；补完整了就立刻回合并后的内容
      ③ 说完整了                  → 直接放行
    """
    msg = ctx.msg
    text = ctx.text
    same = state.merge_sender == msg.sender.id
    fresh = bool(state.merge_buf) and same and \
        (ctx.now - state.merge_ts) <= cfg.typing_wait_sec

    if fresh:
        state.merge_buf = (state.merge_buf + " " + text).strip()[:400]
        state.merge_ts = ctx.now
        merged = state.merge_buf
        waited = ctx.now - state.merge_first_ts
        # 每来一句都重新计时（merge_ts 已更新）；这里只挡"一直不说完"的人
        if looks_unfinished(text) and waited < cfg.typing_max_wait_sec:
            return StageResult("defer", "她还在说，再等 %0.0fs" % cfg.typing_wait_sec, merged)
        return StageResult("pass", "合并后一起回", merged)

    # 新的一串
    state.merge_buf = text
    state.merge_ts = ctx.now
    state.merge_first_ts = ctx.now
    state.merge_sender = msg.sender.id
    if looks_unfinished(text):
        return StageResult("defer", "像是没说完，等一下", text)
    return StageResult("pass", "说完了")


def stage_rate_limit(ctx: MessageCtx, state: ChatState, cfg: GateConfig) -> StageResult:
    """频率：最小间隔 / 同一人连发抑制 / 每小时上限。@她 的消息不做连发抑制。"""
    state.roll_hour(ctx.now)
    if state.hour_count >= cfg.max_auto_per_hour:
        return StageResult("drop", "本小时额度用完（%d/%d）" % (state.hour_count, cfg.max_auto_per_hour))
    # 主人说话、私聊、被 @ —— 都不该被"刚说过话"挡住（人也不会这么对自己的熟人）
    exempt = bool(ctx.msg.at_me or ctx.msg.sender.is_owner
                  or ctx.msg.chat.kind in ("private", "local"))
    gap = ctx.now - state.last_reply_ts
    if state.last_reply_ts and gap < cfg.min_interval_sec and not exempt:
        return StageResult("drop", "刚说过话（%.1fs < %.0fs）" % (gap, cfg.min_interval_sec))
    if (not exempt and state.last_reply_to == ctx.msg.sender.id
            and state.last_reply_ts and gap < cfg.burst_suppress_sec):
        return StageResult("drop", "同一人连发，这轮只回一条")
    return StageResult("pass", "频率 OK")


def stage_decide(ctx: MessageCtx, state: ChatState, cfg: GateConfig) -> Decision:
    """决定回不回。M2 是规则版；M3 会把这一步换成 LLM 的 decide 工具。"""
    msg = ctx.msg
    if msg.chat.kind == "local":
        return Decision("reply", "本地对话", "本地模式一律回")
    if msg.chat.kind == "private":
        # 私聊能走到这儿说明已通过 allowlist（默认只放主人），一律回
        return Decision("reply", "私聊", "私聊里对主人一律回")
    if msg.at_me:
        return Decision("reply", "被叫到", "@她/点名")
    if msg.chat.kind == "room":
        # 房间没有 @ 机制，每条都交给模型判断要不要接（成本高，但房间小）
        return Decision("reply", "房间里有人在说话", "房间不靠 @，交给模型判断")
    if cfg.force_mode == "off":
        return Decision("silence", "", "只认 @，这条不是叫她")
    if msg.chat.kind == "group" and msg.chat.id in cfg.at_only_groups:
        return Decision("silence", "", "本群只认 @")
    p = cfg.offpeak_probability if cfg.force_mode == "offpeak" else cfg.reply_probability
    if random.random() < p:
        return Decision("reply", "", "抽中了（p=%.2f）" % p)
    return Decision("silence", "", "没抽中（p=%.2f）" % p)


def note_replied(state: ChatState, ctx: MessageCtx) -> None:
    """她开口之后更新状态（频率、连发抑制都用它）。"""
    state.last_reply_ts = ctx.now
    state.last_reply_to = ctx.msg.sender.id
    state.roll_hour(ctx.now)
    state.hour_count += 1
    state.merge_buf = ""
    state.merge_ts = 0.0
    state.merge_sender = 0
