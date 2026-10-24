"""会话形态 → 行为策略。

**为什么要这张表**：以前核心到处写 `if kind in ("local", "private")`，
每加一种形态（房间、频道、单播…）都要改好几个文件，等于把"平台知识"塞回核心。
现在新增形态只要在这里加一行。

字段说明：
  personal      是不是"私人场合"（可以用全部工具、记忆更私密）
  exempt_rate   免不免于最小间隔/连发抑制（人少的地方不该晾着对方）
  gate          门控策略：
                  always               一律交给她判断要不要说
                  addressed_or_sample  被叫到才一定考虑，否则按概率抽样
                  auto                 不靠"被叫"，每条都交给她判断
"""

from __future__ import annotations

KINDS: dict[str, dict] = {
    "local": {"personal": True, "exempt_rate": True, "gate": "always"},
    "private": {"personal": True, "exempt_rate": True, "gate": "always"},
    "group": {"personal": False, "exempt_rate": False, "gate": "addressed_or_sample"},
    "room": {"personal": False, "exempt_rate": True, "gate": "auto"},
}

# 未知形态按"群"处理：多人在场、要被打断才算叫她——最保守的默认
DEFAULT = "group"


def policy(kind: str) -> dict:
    return KINDS.get(str(kind or ""), KINDS[DEFAULT])


def is_personal(kind: str) -> bool:
    return bool(policy(kind)["personal"])


def exempt_from_rate(kind: str) -> bool:
    return bool(policy(kind)["exempt_rate"])


def gate_mode(kind: str) -> str:
    return str(policy(kind)["gate"])
