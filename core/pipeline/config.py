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
    merge_window_sec: float = 10.0
    # "话没说完"时，每来一句就重新计时这么久（秒）
    typing_wait_sec: float = 8.0
    # 但总等待不能无限长：从这一串的第一句算起，超过就正常回
    typing_max_wait_sec: float = 40.0
    # 同一人连发抑制（秒）
    burst_suppress_sec: float = 30.0
    # 两次发言最小间隔（秒）
    min_interval_sec: float = 8.0
    # 每会话每小时最多回几条
    max_auto_per_hour: int = 30
    # 非 @ 时的接话概率（0~1）；force_mode=offpeak 时用 offpeak_probability
    reply_probability: float = 0.2
    offpeak_probability: float = 0.25
    # "peak"/"offpeak"/"off"（off = 不按概率接话，只认 @）
    force_mode: str = "off"
    # 这些群即使 force_mode="off"、即使没被 @，也按 reply_probability 参与
    speak_groups: list[int] = field(default_factory=list)
    # 连续对话：她开口后这么久内，跟"同一个人"继续聊不再抽样（0 = 关闭）
    dialogue_window_sec: float = 0.0
    # 连续对话中的最小间隔（比 min_interval_sec 松，多轮才接得上）
    dialogue_min_interval_sec: float = 3.0

    @classmethod
    def from_dict(cls, d: dict) -> GateConfig:
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

        return cls(
            allowed_groups=[int(x) for x in (d.get("allowed_groups") or [])],
            only_when_addressed=[int(x) for x in (d.get("only_when_addressed")
                                                  or d.get("at_only_groups") or [])],
            speak_groups=[int(x) for x in (d.get("speak_groups") or [])],
            dialogue_window_sec=_f("dialogue_window_sec", 0.0),
            dialogue_min_interval_sec=_f("dialogue_min_interval_sec", 3.0),
            private_owner_only=bool(d.get("private_owner_only", False)),
            merge_window_sec=_f("merge_window_sec", 10.0),
            typing_wait_sec=_f("typing_wait_sec", 8.0),
            typing_max_wait_sec=_f("typing_max_wait_sec", 40.0),
            burst_suppress_sec=_f("burst_suppress_sec", 30.0),
            min_interval_sec=_f("min_interval_sec", 8.0),
            max_auto_per_hour=_i("max_auto_per_hour", 30),
            reply_probability=_f("reply_probability", 0.2),
            offpeak_probability=_f("offpeak_probability", 0.25),
            force_mode=str(d.get("force_mode", "off")),
        )
