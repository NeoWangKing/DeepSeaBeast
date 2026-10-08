"""强制工具轮：模型必须调工具，我们执行并把结果回传（最多 max_rounds 轮）。

设计：
  · tool_choice="required" → 每轮模型必调工具，不可能"忘了调 send_message"
  · 发送动作由 agent/tools.py 的 Tools 执行（绑定会话、限频、审计都在那里）
  · 超轮/出错一律收敛为"沉默"，不把思考文字当消息发出去
  · chat_fn 可注入 → tests/agent_loop_sim.py 离线跑
"""
from . import llm as llm_mod

MAX_ROUNDS = 2

REQUIRED = {"send_message": ["text"], "send_sticker": [],
            "web_search": ["query"], "read_url": ["url"],
            "collect_sticker": ["message_id"], "memory_append": ["text"],
            "send_face": ["name"], "view_sticker": ["sticker_id"],
            "sticker_note": ["sticker_id", "note"], "schedule_wake": ["after_sec"]}


def spec_to_openai(specs: list) -> list:
    """把 tools.spec_list() 的扁平参数表转成 OpenAI function schema（带 required）。"""
    out = []
    for name, args, desc, _m in specs:
        props = {}
        for a in args or []:
            one = {k: v for k, v in a.items() if k != "name"}
            props[a["name"]] = one
        out.append({"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props,
                           "required": list(REQUIRED.get(name, []))}}})
    return out


def dispatch(tools, name: str, args: dict, allowed: set) -> str:
    if name not in allowed:
        return "没有这个工具 %s" % name
    fn = getattr(tools, name, None)
    if fn is None:
        return "没有这个工具 %s" % name
    try:
        kw = {k: v for k, v in (args or {}).items() if v is not None}
        return str(fn(**kw))
    except TypeError as e:
        return "参数不对（%s）：请带上正确参数重新调用" % (e,)
    except Exception as e:
        return "调用出错：%r" % (e,)


def run(tools, messages: list, tools_schema: list, chat_fn=None, max_rounds: int = MAX_ROUNDS,
        log=None, model=None, force_first: str = "") -> dict:
    """force_first: 第一轮强制调用某个工具（如 web_search）——把"先查再答"变成结构约束。"""
    """跑一轮"必须用工具"的对话。返回 {rounds, calls, spoke, finished, usage}。"""
    log = log or (lambda *a, **k: None)
    if chat_fn is None:
        chat_fn = (lambda msgs, schema, tc=None:
                   llm_mod.chat_tools(msgs, schema, tc or "required", model=model))
    allowed = {s["function"]["name"] for s in tools_schema}
    calls, usage, rounds = [], {}, 0
    for r in range(1, max(1, int(max_rounds)) + 1):
        rounds = r
        _tc = None
        if r == 1 and force_first:
            _tc = {"type": "function", "function": {"name": str(force_first)}}
            log("loop: 第 1 轮强制调用 %s" % force_first)
        try:
            resp = chat_fn(list(messages), tools_schema, _tc)
        except TypeError:                      # 兼容只收两个参数的桩（离线测试）
            resp = chat_fn(list(messages), tools_schema)
        if resp.get("usage"):
            for k, v in resp["usage"].items():
                if isinstance(v, int):
                    usage[k] = usage.get(k, 0) + v
        tcs = resp.get("tool_calls") or []
        if not tcs:
            log("loop: 第 %d 轮模型没调工具（finish_reason=%s），收敛" % (r, resp.get("finish_reason")))
            break
        messages.append({"role": "assistant", "content": resp.get("content") or None,
                         "tool_calls": [tc.get("raw") or {
                             "id": tc.get("id"), "type": "function",
                             "function": {"name": tc.get("name"),
                                          "arguments": __import__("json").dumps(tc.get("arguments"), ensure_ascii=False)}}
                             for tc in tcs]})
        for tc in tcs:
            name = tc.get("name") or ""
            out = dispatch(tools, name, tc.get("arguments") or {}, allowed)
            calls.append({"round": r, "name": name, "args": tc.get("arguments") or {}, "result": out})
            log("loop: 第 %d 轮 %s(%s) → %s" % (r, name, str(tc.get("arguments"))[:80], out[:60]))
            messages.append({"role": "tool", "tool_call_id": tc.get("id") or "", "content": out})
        # 连发提醒：每多说一轮就提醒一次（只加进下一轮提示，不多花一次模型调用）
        try:
            _said = len([x for x in (tools.sent or []) if str(x[0]) == "text"])
            if not tools.finished and _said >= 2:
                messages.append({"role": "user", "content": (
                    "【系统提醒】你这一轮已经连发 %d 条消息了。群聊里连发很像刷屏："
                    "除非还有**必要**的信息要补，否则现在就调 finish 收工；"
                    "还有话就并进下一条、一次说完。" % _said)})
                log("loop: 第 %d 轮后提醒收尾（这一轮已发 %d 条）" % (r, _said))
        except Exception:
            pass
        if tools.finished:
            # 只有 finish 才收工：允许"先说一句 → 继续查/想 → 再说"（真人就是边想边说）
            # 边界靠 max_rounds，连发则由上面的提醒 + 发言硬上限兜着
            break
    return {"rounds": rounds, "calls": calls, "spoke": bool(tools.spoke),
            "finished": bool(tools.finished), "usage": usage}
