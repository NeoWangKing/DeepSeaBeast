"""场面信号：门控用它决定"要不要问模型"，也顺手喂给模型当上下文。

**分工**：
  · 这里只算**廉价可观测的信号**（不调模型、不花 token）
  · "这话该不该接、跟不跟她有关" —— 交给 decide（模型）
  · 信号既参与打分，也作为一行【场面】写进提示词，让模型判断得体不得体

信号可扩展：加一项 signal 就多一行打分，不需要改流程。
"""

from __future__ import annotations

import re
import time
from collections import deque

# 默认权重（可在 [gate] 或会话覆盖里改）
WEIGHTS: dict[str, float] = {
    "addressed": 3.0,        # 被叫到
    "question": 2.0,         # 直接问句
    "topic_overlap": 2.0,    # 和她刚参与的话题有词重叠
    "name_nearby": 2.0,      # 消息里提到她的名字/外号
    "unanswered": 2.0,       # 有人说话 2 分钟没人接（避免冷场尴尬）
    "cold_room": 1.0,        # 群里安静一阵后的第一条
    "dialogue": 2.0,         # 正在跟她对话的人
    "poke_seen": 1.5,        # 看见有人戳了别人一下（人类会跟着戳同一个）
    "recent_speak": -3.0,    # 她 30 秒内刚说过
    "chatter": -2.0,         # 群里很吵
    "budget": -1.0,          # 接近每小时上限
}


def _tokens(text: str) -> set[str]:
    """粗粒度分词：中文按二元组，英文按词。够用于"话题是否相关"。"""
    t = str(text or "").lower()
    out = set(re.findall(r"[a-z0-9]{2,}", t))
    cjk = re.findall(r"[\u4e00-\u9fff]", t)
    out |= {"".join(cjk[i:i + 2]) for i in range(len(cjk) - 1)}
    return out


class Scene:
    """每个会话的"最近场面"：谁说了什么、有没有人接。"""

    def __init__(self, keep: int = 40) -> None:
        self.buf: deque = deque(maxlen=keep)     # (ts, sender_id, text, answered)
        self.last_room_quiet: float = 0.0

    def note_incoming(self, ts: float, sender_id: int, text: str) -> None:
        self.buf.append([ts, sender_id, str(text or ""), False])

    def note_replied(self, ts: float) -> None:
        """她开口 = 最后一条未被接的话算被接了。"""
        for row in reversed(self.buf):
            if not row[3]:
                row[3] = True
                break

    def unanswered_seconds(self, ts: float) -> float:
        """最后一条"没人接"的消息，已经晾了多久。"""
        for row in reversed(self.buf):
            if not row[3]:
                return max(0.0, ts - row[0])
            return 0.0
        return 0.0

    def chatter_recent(self, ts: float, window: float = 60.0) -> int:
        return sum(1 for row in self.buf if ts - row[0] <= window)

    def quiet_seconds(self, ts: float) -> float:
        if not self.buf:
            return 1e9
        return max(0.0, ts - self.buf[-1][0])

    def last_peer_tokens(self, peer_id: int, limit: int = 3) -> set[str]:
        toks: set[str] = set()
        n = 0
        for ts, sender, text, _ans in reversed(self.buf):
            if sender == peer_id:
                toks |= _tokens(text)
                n += 1
                if n >= limit:
                    break
        return toks


def compute(scene: Scene, msg, identity_names: list[str], dialogue_active: bool,
            budget_ratio: float, now: float | None = None) -> tuple[dict[str, float], list[str]]:
    """算信号，返回 (信号值 0/1, 给模型看的人话描述)。"""
    now = time.time() if now is None else now
    text = str(getattr(msg, "text", "") or "")
    sig: dict[str, float] = {}

    sig["addressed"] = 1.0 if getattr(msg, "addressed", False) else 0.0
    # 看见"戳一戳"：有人戳了某人，她自己可能跟着戳一下
    _segs = getattr(getattr(msg, "message", None), "segments", None) or []
    is_poke = any(getattr(s_, "kind", "") == "poke" for s_ in _segs)
    sig["poke_seen"] = 1.0 if (is_poke and getattr(msg, "focus", None)) else 0.0
    sig["dialogue"] = 1.0 if dialogue_active else 0.0

    # 问句：问号、或者开头是疑问词
    sig["question"] = 1.0 if ("？" in text or "?" in text
                              or re.match(r"^(为什么|怎么|咋|啥|什么|哪|谁|多少|是不是|能不能|可以|要不要|有没有)",
                                          text.strip())) else 0.0

    # 提到她的名字/外号
    names = [n for n in (identity_names or []) if n]
    sig["name_nearby"] = 1.0 if any(n in text for n in names) else 0.0

    # 冷场/被晾
    quiet = scene.quiet_seconds(now)
    sig["cold_room"] = 1.0 if quiet >= 600 else 0.0
    unanswered = scene.unanswered_seconds(now)
    sig["unanswered"] = 1.0 if unanswered >= 120 else 0.0

    # 她刚说过话
    sig["recent_speak"] = 1.0 if getattr(scene, "last_speak_ts", 0) and \
        (now - scene.last_speak_ts) < 30 else 0.0

    # 群里很吵
    sig["chatter"] = 1.0 if scene.chatter_recent(now) > 20 else 0.0
    sig["budget"] = 1.0 if budget_ratio >= 0.8 else 0.0

    hints: list[str] = []
    if sig["unanswered"]:
        hints.append("上一条已经没人接 %d 秒了" % int(unanswered))
    if sig["cold_room"]:
        hints.append("群里刚安静了一阵")
    if sig["chatter"]:
        hints.append("群里正聊得很快")
    if sig["recent_speak"]:
        hints.append("你刚刚才说过话")
    if sig["dialogue"]:
        hints.append("正在跟你聊的人又说话了")
    if sig["poke_seen"]:
        who = getattr(getattr(msg, "focus", None), "name", "") or "对方"
        hints.append("有人刚戳了「%s」一下，你也可以跟着戳" % who)
    return sig, hints


def score(sig: dict[str, float], weights: dict[str, float] | None = None) -> float:
    w = dict(WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items()})
    return sum(w.get(k, 0.0) * v for k, v in sig.items())
