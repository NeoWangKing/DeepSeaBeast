"""会话门控策略：每个会话一份档案（preset + 功能开关）。

设计目标（2026-10-26 重构）：
  · 档位只有三档，语义清楚：strict / balanced / loose
  · 功能开关正交：记忆、历史、工具，各自独立
  · 新需求 = 加一个字段或一个打分信号，不再加全局开关

解析优先级：会话覆盖（[gate.chats."kind:id"]）> 默认 preset > 内置默认值
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# 三档：只决定"进不进 decide"，回不回仍然由模型判断
PRESETS: dict[str, dict] = {
    "strict": {"mode": "strict", "continuation": False},
    "balanced": {"mode": "balanced", "continuation": True},
    "loose": {"mode": "loose", "continuation": True},
}
DEFAULT_PRESET = "balanced"
# 按会话种类的兜底档位：私聊/房间不该因为全局 strict 就哑掉
KIND_DEFAULTS = {"local": "balanced", "private": "balanced", "room": "loose"}

# 功能开关的默认值
DEFAULTS = {
    "memory": "full",      # off / scope / full
    "history": True,       # 上下文里带不带最近聊天
    "tools": "all",        # all / chat_only / none
    "quiet_hours": [],     # ["01:00-08:00"]
}


@dataclass(slots=True)
class ChatPolicy:
    mode: str = "balanced"           # strict / balanced / loose
    continuation: bool = True        # 她开口后能不能"接着聊"
    memory: str = "full"             # off：不读也不写；scope：只用本会话
    history: bool = True             # 带不带最近消息
    tools: str = "all"               # 工具可见性
    quiet_hours: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)

    @property
    def store_memory(self) -> bool:
        """memory=off 的会话**一个字都不落盘**（豹群这类要求）。"""
        return self.memory != "off"

    def weight(self, name: str, default: float) -> float:
        return float(self.weights.get(name, default))

    def quiet_now(self, hhmm: str) -> bool:
        """hhmm='03:20'；支持跨夜的区间（如 23:00-07:00）。"""
        if not self.quiet_hours:
            return False
        try:
            now_min = int(hhmm[:2]) * 60 + int(hhmm[3:5])
        except Exception:
            return False
        for span in self.quiet_hours:
            try:
                a, b = str(span).split("-")
                lo = int(a[:2]) * 60 + int(a[3:5])
                hi = int(b[:2]) * 60 + int(b[3:5])
            except Exception:
                continue
            if lo <= hi:
                if lo <= now_min < hi:
                    return True
            elif now_min >= lo or now_min < hi:      # 跨夜
                return True
        return False


def chat_key(kind: str, chat_id: int) -> str:
    """配置里用的可读键：group:100000001 / private:200000001。"""
    return "%s:%s" % (str(kind or ""), int(chat_id or 0))


def resolve(default_preset: str, overrides: dict, kind: str, chat_id: int) -> ChatPolicy:
    """算出这个会话的最终策略。

    优先级：显式会话覆盖 > 种类兜底（私聊/房间）> 全局 default_preset。
    私聊和房间有自己的天性——不该被全局的"只认 @"一刀切干掉。
    """
    ov0 = (overrides or {}).get(chat_key(kind, chat_id)) or {}
    base_name = str(ov0.get("preset") or KIND_DEFAULTS.get(str(kind))
                    or default_preset or DEFAULT_PRESET)
    base = dict(DEFAULTS)
    base.update(PRESETS.get(base_name, PRESETS[DEFAULT_PRESET]))

    ov = (overrides or {}).get(chat_key(kind, chat_id)) or {}
    if ov:
        preset = str(ov.get("preset") or "")
        if preset in PRESETS:
            base.update(PRESETS[preset])
        for k in ("memory", "history", "tools", "continuation", "quiet_hours"):
            if k in ov:
                base[k] = ov[k]
    return ChatPolicy(
        mode=str(base.get("mode") or "balanced"),
        continuation=bool(base.get("continuation", True)),
        memory=str(base.get("memory") or "full"),
        history=bool(base.get("history", True)),
        tools=str(base.get("tools") or "all"),
        quiet_hours=list(base.get("quiet_hours") or []),
        weights=dict(ov.get("weights") or {}),
    )
