"""通道能力与"诚实失败"。

词汇表**封闭且很小**：加新动词 = 改核心（少见，需要评审）；
实现某个动词 = 改 adapter（常见）。这样核心不会因为"支持更多通道"而膨胀。

原则：通道做不了的事，**报错**，绝不悄悄降级。
降级由"谁想要这个动作"的人决定（插件或模型），不是核心偷偷替它决定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

# 动词（= 能力名）。新增要慎重：所有 adapter 的文档都要跟着变。
TEXT = "text"
AT = "at"
QUOTE = "quote"
IMAGE = "image"
FILE = "file"
FACE = "face"
POKE = "poke"
VOICE = "voice"

ALL_VERBS = (TEXT, AT, QUOTE, IMAGE, FILE, FACE, POKE, VOICE)

# 给模型看的人话（英文动词它未必能对上"发文件"这种说法）
VERB_NAMES = {
    TEXT: "发文字", AT: "@人", QUOTE: "引用回复", IMAGE: "发图片",
    FILE: "发文件", FACE: "发表情", POKE: "戳一戳", VOICE: "发语音",
}


def describe_abilities(abilities: Iterable[str]) -> tuple[list[str], list[str]]:
    """返回（能做的, 做不到的），都是人话。"""
    have = set(abilities)
    can = [VERB_NAMES[v] for v in ALL_VERBS if v in have]
    cannot = [VERB_NAMES[v] for v in ALL_VERBS if v not in have and v != TEXT]
    return can, cannot

# 动作 → 需要的能力
KIND_TO_VERB = {
    "text": TEXT,
    "at": AT,
    "quote": QUOTE,
    "image": IMAGE,
    "file": FILE,
    "face": FACE,
    "poke": POKE,
    "voice": VOICE,
}


# 行内占位符：文字里夹"平台自带表情"的写法。
# **这是核心协议**，不是某个平台的习惯——adapter 负责把它翻译成自己的形状
# （QQ → face 段；没有表情能力的通道压根不会看到它，因为相关工具不会出现）。
FACE_TOKEN_TEMPLATE = "[表情:{name}]"

import re as _re
FACE_TOKEN_RE = _re.compile(r"\[表情:([^\[\]:]{1,12})\]")


def face_token(name: str) -> str:
    return FACE_TOKEN_TEMPLATE.format(name=str(name or "").strip())


class Unsupported(RuntimeError):
    """通道做不了这个动作。"""

    def __init__(self, verb: str, channel: str = "") -> None:
        self.verb = verb
        self.channel = channel
        super().__init__("通道%s不支持 %s" % (("「%s」" % channel) if channel else "", verb))


def supports(abilities: Iterable[str], verb: str) -> bool:
    return verb in set(abilities)


@dataclass(slots=True)
class CapabilityReport:
    """启动时打印的能力矩阵：谁支持什么，哪些工具因此不可用。"""

    channels: dict[str, list[str]]
    unavailable: list[str]          # 形如 "onebot 不支持 file（工具 send_file 需要）"

    def lines(self) -> list[str]:
        out = ["通道能力矩阵："]
        for name, verbs in self.channels.items():
            out.append("  %-10s %s" % (name, " ".join(sorted(verbs))))
        if self.unavailable:
            out.append("能力缺口（这些工具在对应通道里不会出现）：")
            out.extend("  " + x for x in self.unavailable)
        return out


def build_report(channels: list, tools: list) -> CapabilityReport:
    """channels: 有 name/abilities 的对象；tools: 有 name/requires 的对象。"""
    matrix: dict[str, list[str]] = {}
    for c in channels:
        matrix[str(getattr(c, "name", "?"))] = sorted(getattr(c, "abilities", set()))

    missing: list[str] = []
    for c in channels:
        name = str(getattr(c, "name", "?"))
        ab = set(getattr(c, "abilities", set()))
        for t in tools:
            req = set(getattr(t, "requires", set()) or set())
            if req and not req <= ab:
                missing.append("%s 缺 %s（工具 %s 需要）"
                               % (name, "、".join(sorted(req - ab)), getattr(t, "name", "?")))
    return CapabilityReport(channels=matrix, unavailable=missing)
