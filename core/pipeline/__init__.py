"""门控管线：一条消息要不要回、回什么。

每个阶段都是**纯函数**（不改外部状态以外的东西），方便单测：
    stage_allowlist / stage_merge / stage_rate_limit / stage_decide
`Pipeline.run()` 只负责按顺序跑，遇到 drop/defer 就停。
"""

from .config import GateConfig
from .pipeline import Outcome, Pipeline
from .state import ChatState, StateStore
from .types import Decision, StageResult

__all__ = ["Pipeline", "Outcome", "GateConfig", "ChatState", "StateStore",
           "StageResult", "Decision"]
