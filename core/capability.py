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
STICKER = "sticker"      # 表情包（贴图）——不是图片，语义是"情绪"
POKE = "poke"
VOICE = "voice"

ALL_VERBS = (TEXT, AT, QUOTE, IMAGE, FILE, FACE, STICKER, POKE, VOICE)

# 给模型看的人话（英文动词它未必能对上"发文件"这种说法）
VERB_NAMES = {
    TEXT: "发文字", AT: "@人", QUOTE: "引用回复", IMAGE: "发图片",
    FILE: "发文件", FACE: "发表情", STICKER: "发贴图", POKE: "戳一戳", VOICE: "发语音",
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
    "sticker": STICKER,
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


# 贴图在文字里的占位符（她看到别人发贴图时用这个表示）
STICKER_TOKEN_TEMPLATE = "[贴图:{name}]"
STICKER_TOKEN_RE = _re.compile(r"\[贴图:([^\[\]:]{1,16})\]")


def sticker_token(name: str) -> str:
    return STICKER_TOKEN_TEMPLATE.format(name=str(name or "").strip())


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


# 这几个词最容易混，按"当前通道实际有什么工具"给她一份对照表
def glossary(actions: list[str]) -> list[str]:
    """actions 是当前可用的对外动作名（label 或 name）。返回几行解释。"""
    have = set(actions)
    lines = ["【这几个词不一样，别混】",
             "- 表情（emoji）：😂 🥺 这种，直接写在文字里就行",
             "- 颜文字：(｡•ᴗ•｡) (￣▽￣) 这种，也是普通文字，直接写"]
    if "发表情" in have:
        lines.append("- 小表情：平台自带的那种小圆脸/小黄脸，要用「发表情」这个动作")
    if "发贴图" in have:
        lines.append("- 表情包 / 贴图：一张图那种（狗头、捂脸笑），要用「发贴图」这个动作")
    if "发图片" in have:
        lines.append("- 图片 / 照片 / 截图：要用「发图片」这个动作")
    lines.append("- 对方说「发个表情包」= 贴图；只说「表情」= emoji 或小表情，不是贴图")
    if "记待办/定时提醒" in have:
        lines.append("- 对方说「等会儿」「十分钟后」「到 X 点提醒我」→ 用「记待办/定时提醒」记下来，"
                     "到点会自己开口；**别说做不到**")
    return lines


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
