"""工具调用循环：一轮对话最多跑 N 轮；第一轮强制 decide。

设计要点：
  · `tool_choice="required"` 保证模型必须调工具（不会"忘了说话"）
  · 第一轮强制 decide → 不想接就 silence，一句不发
  · 说话必须走 send_message（正文不算数）
  · finish 才收工；轮数上限兜底
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .llm import chat_tools
from .tools import Turn

log = logging.getLogger("agent")


def _assistant_msg(resp) -> dict[str, Any]:
    """把模型的工具调用还原成 OpenAI 的 assistant 消息。"""
    return {
        "role": "assistant",
        "content": resp.content or None,
        "tool_calls": [{"id": c.id or ("call_%d" % i), "type": "function",
                        "function": {"name": c.name,
                                     "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                       for i, c in enumerate(resp.tool_calls)],
    }


async def run_turn(turn: Turn, messages: list[dict[str, Any]],
                   max_rounds: int = 6, force_first: str = "decide") -> Turn:
    """跑一轮：模型 → 工具 → 模型 → … 直到 finish / 说完了 / 轮数用完。"""
    for r in range(1, max(1, int(max_rounds)) + 1):
        choice = "required" if (r == 1 and force_first) else "auto"
        try:
            resp = await asyncio.to_thread(chat_tools, list(messages), turn.schemas,
                                           turn.llm, choice, 400)
        except Exception as e:
            log.warning("模型调用失败（第 %d 轮）：%r", r, e)
            break
        _u = resp.usage or {}
        if _u.get("prompt_tokens"):
            _hit = int(_u.get("prompt_cache_hit_tokens") or 0)
            _tot = int(_u.get("prompt_tokens") or 0)
            log.info("token：命中 %d / 总计 %d（%d%%）", _hit, _tot,
                     (100 * _hit // _tot) if _tot else 0)
        if not resp.tool_calls:
            log.debug("第 %d 轮模型没调工具（finish_reason=%s），收敛", r, resp.finish_reason)
            break
        messages.append(_assistant_msg(resp))
        for call in resp.tool_calls:
            if call.parse_error:
                out = "参数 JSON 解析失败（可能被输出上限截断），重来一次，内容写短点"
            else:
                out = await turn.dispatch(call.name, call.arguments)
                log.info("工具 %s(%s) → %s", call.name,
                         json.dumps(call.arguments, ensure_ascii=False)[:60], str(out)[:60])
            messages.append({"role": "tool", "tool_call_id": call.id or "", "content": out})
        if turn.finished:
            break
    return turn


async def llm_reply_once(llm, messages: list[dict[str, Any]], max_tokens: int = 300) -> str:
    """不带工具的单次回复（备用路径：工具循环失败时兜底，或极简场景）。"""
    from .llm import chat
    return await asyncio.to_thread(chat, messages, llm, max_tokens)
