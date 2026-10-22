"""上下文装配：把"这一轮要喂给模型的东西"做成一份结构化清单，插件可以往里加。

三层内容 + 两类规则：
  persona / rules   —— 核心给（插件**不能删**，persona 只允许 persona 插件覆盖）
  self_knowledge    —— 自身长期记忆（极少变，紧跟 persona，缓存友好）
  scope_knowledge   —— 本群/本人的记忆（每小时变一次）
  scene / recent    —— 每轮变

预算按**估算 token** 算，超了从"低优先级 + 后面"的 knowledge 开始丢。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from .channel import InboundMessage

log = logging.getLogger("context")

# 粗估：中文 1 字≈0.7 token，ASCII 4 字符≈1 token
def estimate_tokens(text: str) -> int:
    s = str(text or "")
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    other = len(s) - cjk
    return int(cjk * 0.7 + other / 4) + 1


@dataclass(slots=True)
class Contribution:
    """插件往上下文里加的东西。priority 越小越先（也越不会被裁）。"""

    priority: int = 50
    knowledge: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    persona: str = ""            # 只有 can_persona=True 的插件才生效


@dataclass(slots=True)
class ContextBundle:
    scope_key: str = ""
    scope_kind: str = ""       # group / private / local
    scope_id: int = 0
    sender_id: int = 0
    sender_name: str = ""
    persona: str = ""
    rules: list[str] = field(default_factory=list)
    self_knowledge: list[str] = field(default_factory=list)
    scope_knowledge: list[tuple[int, str]] = field(default_factory=list)   # (priority, text)
    scene: str = ""
    recent: list[tuple[str, str, int, float]] = field(default_factory=list)
    current: InboundMessage | None = None
    abilities: list[str] = field(default_factory=list)   # 这个通道能做什么
    budget_tokens: int = 6000

    def add_knowledge(self, text: str, priority: int = 50) -> None:
        t = str(text or "").strip()
        if t:
            self.scope_knowledge.append((int(priority), t))

    def tokens(self) -> int:
        parts = [self.persona, self.scene, *self.rules, *self.self_knowledge]
        parts += [t for _, t in self.scope_knowledge]
        parts += [x[1] for x in self.recent]
        if self.current is not None:
            parts.append(self.current.text)
        return sum(estimate_tokens(p) for p in parts)

    def trim(self) -> None:
        """超预算就丢 scope_knowledge 里"优先级最低、排最后"的那些。"""
        while self.tokens() > self.budget_tokens and self.scope_knowledge:
            worst = max(range(len(self.scope_knowledge)),
                        key=lambda i: (self.scope_knowledge[i][0], i))
            dropped = self.scope_knowledge.pop(worst)[1]
            log.info("上下文预算紧张，丢掉一条低优先级素材：%r", dropped[:30])


class ContextProvider(Protocol):
    """插件实现这个接口，就能往上下文里加料。"""

    name: str
    priority: int
    can_persona: bool

    async def contribute(self, bundle: ContextBundle) -> Contribution | None: ...


class ContextRegistry:
    def __init__(self) -> None:
        self._providers: list[Any] = []

    def add(self, provider: Any) -> None:
        self._providers.append(provider)
        self._providers.sort(key=lambda p: int(getattr(p, "priority", 50)))

    async def collect(self, bundle: ContextBundle) -> ContextBundle:
        for p in self._providers:
            name = getattr(p, "name", "?")
            try:
                c = await p.contribute(bundle)
            except Exception:
                log.exception("上下文插件 %s 出错（跳过）", name)
                continue
            if not c:
                continue
            if c.persona and getattr(p, "can_persona", False):
                bundle.persona = str(c.persona).strip()
            for r in c.rules:
                r = str(r or "").strip()
                if r and r not in bundle.rules:
                    bundle.rules.append(r)
            for k in c.knowledge:
                bundle.add_knowledge(k, int(getattr(p, "priority", 50)))
        return bundle
