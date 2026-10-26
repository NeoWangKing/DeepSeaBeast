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
import time
from dataclasses import dataclass, field
from typing import Any

from .capability import (KIND_TO_VERB, TEXT, Unsupported, face_token, glossary,
                         supports)
from .channel import InboundMessage, Message, Segment

log = logging.getLogger("tools")


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    # 需要的 adapter 能力，比如 {"face"}；空 = 任何渠道都能用
    requires: set[str] = field(default_factory=set)
    # action=对外动作（算"她会做什么"）；query=查询辅助；control=内部控制流（decide/finish）
    label: str = ""          # 中文名（给模型看的清单里用）
    kind: str = "action"
    # ready=已验证可用；experimental=写了但没真机验证（不给模型）；planned=只有想法
    status: str = "ready"


# 纯聊天也需要的最小工具集（tools=none 时只留这些）
CHAT_ONLY = {"decide", "send_message", "finish"}
# chat_only：加轻量表达（表情），但不能发文件/贴图/待办
CHAT_EXPRESSIVE = CHAT_ONLY | {"send_face", "list_faces"}


def visible(specs: list[ToolSpec], mode: str) -> list[ToolSpec]:
    """按会话的 tools 开关过滤：all / chat_only / none。"""
    mode = str(mode or "all")
    if mode == "all":
        return list(specs)
    allow = CHAT_EXPRESSIVE if mode == "chat_only" else CHAT_ONLY
    return [s for s in specs if s.name in allow]


def schema(specs: list[ToolSpec], abilities: set[str]) -> list[dict[str, Any]]:
    """按渠道能力 + 验证状态过滤后，转成 OpenAI function schema。"""
    out: list[dict[str, Any]] = []
    for s in specs:
        if s.status != "ready":
            continue
        if s.requires and not s.requires <= abilities:
            continue
        out.append({"type": "function", "function": {
            "name": s.name, "description": s.description, "parameters": s.parameters}})
    return out


DECIDE = ToolSpec(
    name="decide", label="判断接不接",
    description="**每轮第一件事**：先决定这条要不要接。不想接就 action=silence，一句都不发。",
    parameters={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["reply", "wait", "silence"],
                   "description": "reply=要回 / wait=先等等 / silence=不接"},
        "target": {"type": "string", "description": "在接谁的话（可选）"},
        "intent": {"type": "string", "description": "一句话说你想表达什么（可选）"},
    }, "required": ["action"]},
    kind="control",
)

SEND_MESSAGE = ToolSpec(
    name="send_message", label="说话",
    description="发言：**你写的正文不会发出去，说话只能调这个工具**。想发几条就调几次。",
    parameters={"type": "object", "properties": {
        "text": {"type": "string", "description": "要说的话"},
        "quote_id": {"type": "string", "description": "可选：引用哪条消息"},
        "at": {"type": "integer", "description": "可选：@ 谁的账号 id（通道支持才行）"},
    }, "required": ["text"]},
)

ADD_TODO = ToolSpec(
    name="add_todo", label="记待办/定时提醒", kind="action",
    description="把答应要做的事记下来，到点会自己开口（跨回合、跨重启都在）。"
                "对方说「等会儿」「十分钟后」「到 X 点」都记成待办——**别说做不到**。",
    parameters={"type": "object", "properties": {
        "text": {"type": "string", "description": "要做什么，一句话"},
        "due_minutes": {"type": "number", "description": "可选：多少分钟后提醒"},
        "due_time": {"type": "string",
                     "description": "可选：具体时刻，写 HH:MM（今天）或 MM-DD HH:MM"},
    }, "required": ["text"]},
)

LIST_TODOS = ToolSpec(
    name="list_todos", label="看自己的待办", kind="query",
    description="看看自己手头还压着哪些没做完的事。",
    parameters={"type": "object", "properties": {}},
)

DONE_TODO = ToolSpec(
    name="done_todo", label="待办收掉", kind="control",
    description="做完了一件事就把它收掉。全部收完说明可以接新活了。",
    parameters={"type": "object", "properties": {
        "id": {"type": "string", "description": "待办 id（list_todos 里有）"}},
        "required": ["id"]},
)

DROP_TODO = ToolSpec(
    name="drop_todo", label="待办作废", kind="control",
    description="这件事不做了/做不了，作废掉（别留着假装还在做）。",
    parameters={"type": "object", "properties": {
        "id": {"type": "string"}}, "required": ["id"]},
)

FINISH = ToolSpec(
    name="finish", label="收工",
    description="这轮说完了就调它收工，不要留话。",
    parameters={"type": "object", "properties": {
        "reason": {"type": "string", "description": "为什么收工（可选）"}}},
    kind="control",
)

SEND_FILE = ToolSpec(
    name="send_file", label="发文件",
    description="把本机上的一个文件发到当前会话（**只有支持文件的通道才有这个工具**）",
    parameters={"type": "object", "properties": {
        "path": {"type": "string", "description": "文件的绝对路径"},
        "name": {"type": "string", "description": "可选：显示的文件名"},
    }, "required": ["path"]},
    requires={"file"},
)

SEND_IMAGE = ToolSpec(
    name="send_image", label="发图片",
    description="发一张图片（本地路径或直链 URL；**只有支持图片的通道才有**）",
    parameters={"type": "object", "properties": {
        "path": {"type": "string", "description": "本地图片路径"},
        "url": {"type": "string", "description": "图片直链"},
        "caption": {"type": "string", "description": "可选：配一句话"},
    }},
    requires={"image"}, status="experimental",
)

SEND_FACE = ToolSpec(
    name="send_face", label="发表情",
    description="发一个平台自带的**小表情**（那种小圆脸/小黄脸）。"
                "**不是表情包/贴图**——对方要「表情包」「贴图」时用 send_sticker。"
                "名字必须是平台真有的，不确定就先 list_faces 查。",
    parameters={"type": "object", "properties": {
        "name": {"type": "string", "description": "表情名字，如「坏笑」「托腮」"}},
        "required": ["name"]},
    requires={"face"},
)

LIST_FACES = ToolSpec(
    name="list_faces", label="查表情名",
    description="查这个平台有哪些表情可用（可按关键词过滤）。名字不记得时先查再发。",
    parameters={"type": "object", "properties": {
        "keyword": {"type": "string", "description": "可选：按关键词过滤，如「狗」「笑哭」"}}},
    requires={"face"}, kind="query",
)

DEFAULT_SPECS = [DECIDE, SEND_MESSAGE, SEND_FACE, LIST_FACES, SEND_FILE, SEND_IMAGE,
                 ADD_TODO, LIST_TODOS, DONE_TODO, DROP_TODO, FINISH]


def skill_line(specs: list[ToolSpec], abilities: set[str]) -> str:
    """给模型看的"你手上现在能做什么"。

    只列**对外动作**（action）和**查询辅助**（query）；
    内部控制流（decide/finish）不是能力，永远不出现。
    """
    ready = [s for s in specs if s.status == "ready"
             and (not s.requires or s.requires <= abilities)]
    actions = [s.label or s.name for s in ready if s.kind == "action"]
    queries = [s.label or s.name for s in ready if s.kind == "query"]
    if not actions and not queries:
        return ""
    parts = ["你手上现在能做：" + "、".join(actions) + "。"]
    if queries:
        parts.append("另外还能查：" + "、".join(queries) + "。")
    parts.append("没列出来的就是做不到——直接说做不到，别答应、别假装做了。")
    return "".join(parts)


class Turn:
    """一轮对话的上下文：她能调用的工具 + 已经发出去的话。"""

    def __init__(self, channel, msg: InboundMessage, llm, persona: str = "",
                 specs: list[ToolSpec] | None = None, max_sends: int = 4,
                 plugins=None, abilities: set[str] | None = None,
                 ctx=None, faces_catalog: list[str] | None = None,
                 todos=None, scope_key: str = "", tools_mode: str = "all") -> None:
        self.channel = channel
        self.msg = msg
        self.llm = llm
        self.persona = persona
        self.tools_mode = str(tools_mode or "all")
        self.specs = visible(list(specs or DEFAULT_SPECS), self.tools_mode)
        self.ctx = ctx                          # TurnContext（预算/取消/追踪）
        self.max_sends = int(getattr(ctx, "cfg").max_sends) if ctx is not None else max_sends
        self.plugins = plugins            # PluginManager（可选）
        # 由调用方按"这条消息走哪个通道"决定；缺省用 channel 自己的
        self.faces_catalog: list[str] = list(faces_catalog or [])   # 平台全部表情名
        self.abilities = set(abilities) if abilities is not None \
            else set(getattr(channel, "abilities", set()))
        self.decision: dict[str, Any] | None = None
        self.sent: list[str] = []
        self.finished = False
        self.finish_reason = ""
        self.errors: list[str] = []
        self.todos = todos                      # TodoStore（可选）
        self.scope_key = scope_key or str(getattr(msg.chat, "key", "") or "")
        self._spec_by_name = {s.name: s for s in self.specs}

    def skill_text(self) -> str:
        """合并核心工具和插件工具，渲染成"你手上现在能做"那一行。"""
        specs = list(self.specs)
        if self.plugins is not None and self.tools_mode == "all":
            for t in getattr(self.plugins, "tools", {}).values():
                specs.append(ToolSpec(name=t.name, description=t.description,
                                      parameters={}, requires=set(t.requires),
                                      label=getattr(t, "label", "") or t.name,
                                      kind=getattr(t, "kind", "action")))
        line = skill_line(specs, self.abilities)
        if not line:
            return ""
        ready = [s for s in specs if s.status == "ready"
                 and (not s.requires or s.requires <= self.abilities)]
        actions = [s.label or s.name for s in ready if s.kind == "action"]
        return line + "\n" + "\n".join(glossary(actions))

    @property
    def schemas(self) -> list[dict[str, Any]]:
        ab = self.abilities
        out = schema(self.specs, ab)
        if self.plugins is not None and self.tools_mode == "all":
            out.extend(self.plugins.schemas(ab))
        return out

    # ── 工具分发 ──
    async def dispatch(self, name: str, args: dict[str, Any]) -> str:
        if name not in self._spec_by_name:
            if self.plugins is not None and self.plugins.has(name):
                try:
                    res = await self.plugins.call(name, args)
                    # 协议 v1：{"ok": true, "data": ...} / {"ok": false, "error": ...}
                    if isinstance(res, dict) and "ok" in res:
                        if not res.get("ok"):
                            return "插件返回错误：%s" % (res.get("error") or "未知")
                        data = res.get("data")
                        # 插件不直接碰通道：它给"意图"，核心负责执行
                        note = await self._run_intents(data)
                        if note:
                            return note
                        return data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
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
            if name == "send_face":
                return await self._do_send_face(args)
            if name == "list_faces":
                return self._do_list_faces(args)
            if name == "send_file":
                return await self._do_send_file(args)
            if name == "send_image":
                return await self._do_send_image(args)
            if name == "add_todo":
                return self._do_add_todo(args)
            if name == "list_todos":
                return self._do_list_todos(args)
            if name == "done_todo":
                return self._do_done_todo(args)
            if name == "drop_todo":
                return self._do_drop_todo(args)
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

    async def _run_intents(self, data: Any) -> str:
        """插件在 data.send 里给动作列表，核心按通道能力执行。

            {"send": [{"kind": "text", "text": "看看这个"},
                      {"kind": "file", "path": "/tmp/x.log", "name": "x.log"}]}

        通道做不了 → 原样告诉模型（**不静默降级**），由它用语言兜底。
        """
        if not isinstance(data, dict):
            return ""
        intents = data.get("send")
        if not isinstance(intents, list) or not intents:
            return ""
        done: list[str] = []
        for it in intents:
            if not isinstance(it, dict):
                continue
            kind = str(it.get("kind") or "text")
            verb = KIND_TO_VERB.get(kind, TEXT)
            if not supports(self.abilities, verb):
                return "这个通道做不到「%s」（能力缺失），请用文字表达" % verb
            try:
                if kind == "text":
                    await self._do_send({"text": str(it.get("text") or "")})
                    done.append("text")
                elif kind == "file":
                    seg = Segment("file", text="[文件]",
                                  data={"path": str(it.get("path") or ""),
                                        "name": str(it.get("name") or "")})
                    r = await self._send_message_out([seg])
                    if not r:
                        return "文件发送失败"
                    done.append("file")
                elif kind == "sticker":
                    seg = Segment("sticker", text="[贴图]",
                                  data={"path": str(it.get("path") or ""),
                                        "id": str(it.get("id") or ""),
                                        "name": str(it.get("name") or "")})
                    ok, err = await self._send_message_out([seg])
                    if not ok:
                        return "贴图发送失败：%s" % (err or "未知原因")
                    done.append("sticker")
                elif kind == "image":
                    seg = Segment("image", text="[图片]",
                                  data={"path": str(it.get("path") or ""),
                                        "url": str(it.get("url") or "")})
                    ok, err = await self._send_message_out([seg])
                    if not ok:
                        return "图片发送失败：%s" % (err or "未知原因")
                    done.append("image")
                else:
                    return "不认识的发送动作：%s" % kind
            except Unsupported as e:
                return str(e)
        return "已执行：%s" % "、".join(done) if done else ""

    async def _do_send_face(self, args: dict[str, Any]) -> str:
        if self.ctx is not None and self.ctx.should_stop():
            return "这一轮已经结束了，不要再发"
        name = str(args.get("name") or "").strip()
        if not name:
            return "name 不能为空"
        # 行内写法交给通道去替换成原生表情；这里单独发一条
        r = await self._do_send({"text": face_token(name)})
        if "已发出" not in r:
            return "表情没发出去：%s" % r
        return "表情已发出"

    def _do_list_faces(self, args: dict[str, Any]) -> str:
        names = self.faces_catalog or []
        kw = str(args.get("keyword") or "").strip()
        if kw:
            names = [n for n in names if kw in n]
        if not names:
            return "这个平台没有可用的表情" if not kw else "没找到匹配「%s」的表情" % kw
        return "可用表情（%d 个）：%s" % (len(names), "、".join(names[:60]))

    async def _do_send_file(self, args: dict[str, Any]) -> str:
        if self.ctx is not None and self.ctx.should_stop():
            return "这一轮已经结束了，不要再发"
        path = str(args.get("path") or "")
        if not path:
            return "path 不能为空"
        seg = Segment("file", text="[文件]", data={"path": path,
                                                  "name": str(args.get("name") or "")})
        ok, err = await self._send_message_out([seg])
        if not ok:
            return "文件没发出去：%s" % (err or "通道不支持")
        self.sent.append("[文件]%s" % path.rsplit("/", 1)[-1])
        if self.ctx is not None:
            self.ctx.sends += 1
        return "文件已发出"

    async def _do_send_image(self, args: dict[str, Any]) -> str:
        if self.ctx is not None and self.ctx.should_stop():
            return "这一轮已经结束了，不要再发"
        path = str(args.get("path") or "")
        url = str(args.get("url") or "")
        if not path and not url:
            return "path 或 url 至少给一个"
        caption = str(args.get("caption") or "")
        if caption:
            await self._do_send({"text": caption})
        seg = Segment("image", text="[图片]", data={"path": path, "url": url})
        ok, err = await self._send_message_out([seg])
        if not ok:
            return "图片没发出去：%s" % (err or "通道不支持")
        self.sent.append("[图片]")
        if self.ctx is not None:
            self.ctx.sends += 1
        return "图片已发出"

    async def _send_message_out(self, segments: list[Segment]) -> tuple[bool, str]:
        """发一条消息，返回（成功与否, 错误原因）。"""
        from .channel import OutboundMessage
        out = OutboundMessage(chat=self.msg.chat, message=Message(segments=segments),
                              quote_id="", at_user=0)
        res = await self.channel.send(out)
        ok = bool(getattr(res, "ok", False))
        err = str(getattr(res, "error", "") or "")
        confirmed = bool(getattr(res, "confirmed", True))
        if not ok:
            log.warning("发送失败：%s", err or "未知原因")
        elif not confirmed:
            # 发出去了但没法确认：重试可能重复，所以只记录、不重试
            log.info("发送未确认（可重试性：不安全）：%s", err or "通道无回执")
        return ok, err

    # ── 待办 ──────────────────────────────────────────
    def _do_add_todo(self, args: dict[str, Any]) -> str:
        if self.todos is None:
            return "这个环境没开待办功能"
        due = None
        mins = args.get("due_minutes")
        if isinstance(mins, (int, float)) and mins > 0:
            due = time.time() + float(mins) * 60
        spec = str(args.get("due_time") or "").strip()
        if due is None and spec:
            due = self._parse_due(spec)
        todo, note = self.todos.add(self.scope_key, str(args.get("text") or ""),
                                    due=due, source="她记的")
        if todo is None:
            return note or "记不下来"
        left = len(self.todos.open_items(self.scope_key))
        return "记下了（%s，手头共 %d 件）%s" % (todo.id, left, note)

    @staticmethod
    def _parse_due(spec: str) -> float | None:
        """把 "14:53" / "10-24 08:00" 解析成时间戳（今天已过就顺延到明天）。"""
        import datetime as _dt
        now = _dt.datetime.now()
        for fmt, guess in (("%H:%M", "today"), ("%H:%M:%S", "today"),
                           ("%m-%d %H:%M", "year"), ("%Y-%m-%d %H:%M", None)):
            try:
                d = _dt.datetime.strptime(spec, fmt)
            except ValueError:
                continue
            if guess == "today":
                d = d.replace(year=now.year, month=now.month, day=now.day)
                if d <= now:
                    d += _dt.timedelta(days=1)
            elif guess == "year":
                d = d.replace(year=now.year)
                if d <= now:
                    d = d.replace(year=now.year + 1)
            return d.timestamp()
        return None

    def _do_list_todos(self, args: dict[str, Any]) -> str:
        if self.todos is None:
            return "这个环境没开待办功能"
        items = self.todos.open_items(self.scope_key)
        if not items:
            return "手头没有没做完的事"
        rows = []
        for t in items:
            when = ""
            if t.due:
                left = max(0, int(t.due - time.time()))
                when = "（%d 分钟后提醒）" % (left // 60 or 1) if left else "（已到点）"
            rows.append("%s  %s%s" % (t.id, t.text, when))
        return "手头 %d 件：\n%s" % (len(items), "\n".join(rows))

    def _do_done_todo(self, args: dict[str, Any]) -> str:
        if self.todos is None:
            return "这个环境没开待办功能"
        ok, msg = self.todos.done(self.scope_key, str(args.get("id") or ""))
        return msg if ok else "出错了：%s" % msg

    def _do_drop_todo(self, args: dict[str, Any]) -> str:
        if self.todos is None:
            return "这个环境没开待办功能"
        ok, msg = self.todos.drop(self.scope_key, str(args.get("id") or ""))
        return msg if ok else "出错了：%s" % msg

    async def _do_send(self, args: dict[str, Any]) -> str:
        if self.ctx is not None:
            stop = self.ctx.should_stop()
            if stop:
                return "这一轮已经结束了（%s），不要再发" % stop
        if not self.decision or self.decision.get("action") != "reply":
            return "先调 decide(action=reply) 再说话：这条到底要不要接？"
        if len(self.sent) >= self.max_sends:
            return "这轮已经发了 %d 条，够了，调 finish 收工" % len(self.sent)
        text = str(args.get("text") or "").strip()
        if not text:
            return "text 是空的"
        segs = [Segment("text", text=text)]
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
        if self.ctx is not None:
            self.ctx.sends += 1
        return "已发出（%d/%d）" % (len(self.sent), self.max_sends)
