"""门控配置（core__custom__.toml 的 [gate] 段）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class GateConfig:
    """一条消息要不要回的全部开关。默认值偏"克制"：只在被叫时回。"""

    # 白名单：留空 = 所有群都管
    allowed_groups: list[int] = field(default_factory=list)
    # 只认 @ 的群（其余群允许按概率接话）
    # 这些会话只在"被叫到"时才考虑回话（按通道给的会话 id 配，不是平台专用概念）
    only_when_addressed: list[int] = field(default_factory=list)
    # 私聊是否只回主人（默认 false：私聊都理，靠频率限制防刷）
    private_owner_only: bool = False

    # 连发合并窗口（秒）
    # "话没说完"时，每来一句就重新计时这么久（秒）
    typing_wait_sec: float = 8.0
    # 但总等待不能无限长：从这一串的第一句算起，超过就正常回
    typing_max_wait_sec: float = 40.0
    # 同一人连发抑制（秒）
    # 两次发言最小间隔（秒）
    # 每会话每小时最多回几条
    max_auto_per_hour: int = 30
    # "peak"/"offpeak"/"off"（off = 不按概率接话，只认 @）
    force_mode: str = "off"
    # 这些群即使 force_mode="off"、即使没被 @，也按 reply_probability 参与
    speak_groups: list[int] = field(default_factory=list)
    # ── 2026-10 门控重构：每会话策略 ──
    # 默认档位：strict（只认被叫到）/ balanced（被叫必问，否则打分）/ loose（每条都问，受打分约束）
    default_preset: str = "balanced"
    # 每会话覆盖：[gate.chats."group:100000001"] preset/memory/history/tools/continuation
    chats: dict = field(default_factory=dict)
    # 发言额度：最多攒几点、每多少分钟回充 1 点
    credit_max: float = 3.0
    credit_refill_min: float = 15.0
    # 打分阈值（带 ±1 抖动）
    score_threshold: float = 2.0
    # 每小时最多进几次 decide（成本硬上限）
    max_decide_per_hour: int = 30
    # 连续对话：最多几个来回、窗口多久、对话中的最小间隔、额度回充加速
    dialogue_max_turns: int = 6
    dialogue_refill_boost: float = 2.0
    # 连续对话：她开口后这么久内，跟"同一个人"继续聊不再抽样（0 = 关闭）
    dialogue_window_sec: float = 0.0

    @classmethod
    def from_dict(cls, d: dict) -> GateConfig:
        """读 [gate] 段；旧键（force_mode/speak_groups/only_when_addressed）
        自动翻译成新格式，老配置不用改也能跑。"""
        d = dict(d or {})
        def _i(key: str, default: int) -> int:
            try:
                return int(d.get(key, default))
            except Exception:
                return default

        def _f(key: str, default: float) -> float:
            try:
                return float(d.get(key, default))
            except Exception:
                return default

        chats = dict(d.get("chats") or {})
        preset = str(d.get("default_preset") or "")
        if not chats or not preset:
            # 旧格式兼容：speak_groups → loose；only_when_addressed → strict
            speak = [int(x) for x in (d.get("speak_groups") or [])]
            only = [int(x) for x in (d.get("only_when_addressed")
                                     or d.get("at_only_groups") or [])]
            for gid in speak:
                chats.setdefault("group:%d" % gid, {"preset": "loose"})
            for gid in only:
                if gid not in speak:
                    chats.setdefault("group:%d" % gid, {"preset": "strict"})
            if not preset:
                preset = "balanced" if str(d.get("force_mode") or "off") != "off" \
                    else "strict"

        return cls(
            allowed_groups=[int(x) for x in (d.get("allowed_groups") or [])],
            only_when_addressed=[int(x) for x in (d.get("only_when_addressed")
                                                  or d.get("at_only_groups") or [])],
            speak_groups=[int(x) for x in (d.get("speak_groups") or [])],
            default_preset=preset,
            chats=chats,
            credit_max=_f("credit_max", 3.0),
            credit_refill_min=_f("credit_refill_min", 15.0),
            score_threshold=_f("score_threshold", 2.0),
            max_decide_per_hour=_i("max_decide_per_hour", 30),
            dialogue_max_turns=_i("dialogue_max_turns", 6),
            dialogue_refill_boost=_f("dialogue_refill_boost", 2.0),
            dialogue_window_sec=_f("dialogue_window_sec", 0.0),
            private_owner_only=bool(d.get("private_owner_only", False)),
            typing_wait_sec=_f("typing_wait_sec", 8.0),
            typing_max_wait_sec=_f("typing_max_wait_sec", 40.0),
            max_auto_per_hour=_i("max_auto_per_hour", 30),
            force_mode=str(d.get("force_mode", "off")),
        )
