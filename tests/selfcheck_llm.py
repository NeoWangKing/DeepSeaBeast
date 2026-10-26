#!/usr/bin/env python3
"""真模型连通性自测（会花钱，几厘钱）。

用法：.venv/bin/python tests/selfcheck_llm.py
key 默认读 ~/Documents/Deepseek_APIs/qq_key1（可用 [llm] 配置或环境变量覆盖）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import load as load_config  # noqa: E402
from core.llm import chat, chat_tools, LLMConfig  # noqa: E402

from core.config import find_config as _find_cfg
_cfg_path = _find_cfg(str(ROOT))
cfg_core = load_config(_cfg_path) if _cfg_path else None
llm_cfg = LLMConfig.from_dict(getattr(cfg_core, "llm", {}) or {})
key = llm_cfg.resolve_key()
print("模型：%s @ %s | key: %s" % (llm_cfg.model, llm_cfg.base_url,
                                 ("%s…%s" % (key[:6], key[-4:])) if key else "（没读到）"))
if not key:
    print("跳过：没有 key")
    raise SystemExit(0)

print("== 普通对话 ==")
t = chat([{"role": "system", "content": "你是群里的朋友，说话简短口语化，别像客服。"},
          {"role": "user", "content": "在吗"}], llm_cfg, 120)
print("回复：", t[:120])

print("== 工具调用 ==")
tools = [{"type": "function", "function": {
    "name": "send_message",
    "description": "在群里说一句话",
    "parameters": {"type": "object", "properties": {
        "text": {"type": "string", "description": "要说的话"}}, "required": ["text"]}}}]
r = chat_tools([{"role": "system", "content": "你要说话只能调 send_message 工具。"},
                {"role": "user", "content": "跟大家打个招呼"}],
               tools, llm_cfg, tool_choice="required", max_tokens=200)
call = r.tool_calls[0] if r.tool_calls else None
print("工具调用：", (call.name, call.arguments) if call else "（没有调用）")
print("usage：", r.usage)
ok = bool(t) and call is not None and call.name == "send_message"
print("全部通过" if ok else "FAILED")
raise SystemExit(0 if ok else 1)
