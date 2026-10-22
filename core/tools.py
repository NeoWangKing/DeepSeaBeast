"""工具：她**只能**通过工具说话。

内置三个：
  decide(action, target, intent)  第一轮先决定要不要接（reply / wait / silence）
  send_message(text, quote_id, at) 真正发言（一次调用 = 一条消息）
  finish(reason)                   收工

插件以后往同一个注册表里加工具（带 `requires` 声明所需能力，CLI 里没的能力自动隐藏）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .channel import InboundMessage, Message, Segment

log = logging.getLogger("tools")


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    # 需要的 adapter 能力，比如 {"face"}；空 = 任何渠道都能用
    requires: set[str] = field(default_factory=set)


def schema(specs: list[ToolSpec], abilities: set[str]) -> list[dict[str, Any]]:
    """按渠道能力过滤后，转成 OpenAI function schema。"""
    out: list[dict[str, Any]] = []
    for s in specs:
        if s.requires and not s.requires <= abilities:
            continue
        out.append({"type": "function", "function": {
            "name": s.name, "description": s.description, "parameters": s.parameters}})
    return out


DECIDE = ToolSpec(
    name="decide",
    description="**每轮第一件事**：先决定这条要不要接。不想接就 action=silence，一句都不发。",
    parameters={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["reply", "wait", "silence"],
                   "description": "reply=要回 / wait=先等等 / silence=不接"},
        "target": {"type": "string", "description": "在接谁的话（可选）"},
        "intent": {"type": "string", "description": "一句话说你想表达什么（可选）"},
    }, "required": ["action"]},
)

SEND_MESSAGE = ToolSpec(
    name="send_message",
    description="发言：**你写的正文不会发出去，说话只能调这个工具**。想发几条就调几次。",
    parameters={"type": "object", "properties": {
        "text": {"type": "string", "description": "要说的话"},
        "quote_id": {"type": "string", "description": "可选：引用哪条消息"},
        "at": {"type": "integer", "description": "可选：@ 谁的 QQ"},
    }, "required": ["text"]},
)

FINISH = ToolSpec(
    name="finish",
    description="这轮说完了就调它收工，不要留话。",
    parameters={"type": "object", "properties": {
        "reason": {"type": "string", "description": "为什么收工（可选）"}}},
)

DEFAULT_SPECS = [DECIDE, SEND_MESSAGE, FINISH]


class Turn:
    """一轮对话的上下文：她能调用的工具 + 已经发出去的话。"""

    def __init__(self, channel, msg: InboundMessage, llm, persona: str = "",
                 specs: list[ToolSpec] | None = None, max_sends: int = 4,
                 plugins=None, abilities: set[str] | None = None) -> None:
        self.channel = channel
        self.msg = msg
        self.llm = llm
        self.persona = persona
        self.specs = list(specs or DEFAULT_SPECS)
        self.max_sends = max_sends
        self.plugins = plugins            # PluginManager（可选）
        # 由调用方按"这条消息走哪个通道"决定；缺省用 channel 自己的
        self.abilities = set(abilities) if abilities is not None \
            else set(getattr(channel, "abilities", set()))
        self.decision: dict[str, Any] | None = None
        self.sent: list[str] = []
        self.finished = False
        self.finish_reason = ""
        self.errors: list[str] = []
        self._spec_by_name = {s.name: s for s in self.specs}

    @property
    def schemas(self) -> list[dict[str, Any]]:
        ab = self.abilities
        out = schema(self.specs, ab)
        if self.plugins is not None:
            out.extend(self.plugins.schemas(ab))
        return out

    # ── 工具分发 ──
    async def dispatch(self, name: str, args: dict[str, Any]) -> str:
        if name not in self._spec_by_name:
            if self.plugins is not None and self.plugins.has(name):
                try:
                    res = await self.plugins.call(name, args)
                    return res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)
                except Exception as e:
                    log.warning("插件工具 %s 失败：%r", name, e)
                    return "插件工具出错：%r" % (e,)
            return "没有这个工具：%s" % name
        try:
            if name == "decide":
                return self._do_decide(args)
            if name == "send_message":
                return await self._do_send(args)
            if name == "finish":
                self.finished = True
                self.finish_reason = str(args.get("reason") or "")
                return "收工"
            return "工具 %s 没有实现" % name
        except Exception as e:
            self.errors.append("%s: %r" % (name, e))
            log.warning("工具 %s 出错：%r", name, e)
            return "调用出错：%r" % (e,)

    def _do_decide(self, args: dict[str, Any]) -> str:
        action = str(args.get("action") or "").strip().lower()
        if action not in ("reply", "wait", "silence"):
            return "decide 的 action 只能是 reply / wait / silence"
        self.decision = {"action": action,
                         "target": str(args.get("target") or "")[:40],
                         "intent": str(args.get("intent") or "")[:120]}
        if action in ("wait", "silence"):
            self.finished = True
            return "好，这条不接（%s），别调用 send_message。" % action
        return "行，按这个回：%s" % (self.decision["intent"] or "顺着上下文接一句")

    async def _do_send(self, args: dict[str, Any]) -> str:
        if not self.decision or self.decision.get("action") != "reply":
            return "先调 decide(action=reply) 再说话：这条到底要不要接？"
        if len(self.sent) >= self.max_sends:
            return "这轮已经发了 %d 条，够了，调 finish 收工" % len(self.sent)
        text = str(args.get("text") or "").strip()
        if not text:
            return "text 是空的"
        segs = [Segment("text", {"text": text})]
        if self.msg.chat.kind != "local" or True:
            pass
        quote_id = str(args.get("quote_id") or "")
        at = int(args.get("at") or 0)
        from .channel import OutboundMessage
        out = OutboundMessage(chat=self.msg.chat, message=Message(segments=segs),
                              quote_id=quote_id, at_user=at)
        res = await self.channel.send(out)
        if not res.ok:
            return "发送失败：%s" % res.error
        self.sent.append(text)
        return "已发出（%d/%d）" % (len(self.sent), self.max_sends)
