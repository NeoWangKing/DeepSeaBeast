"""OpenAI 兼容的模型客户端（纯标准库 urllib，不引第三方 SDK）。

只要 base_url 兼容 `/chat/completions`，DeepSeek / 硅基流动 / 本地 vLLM 都能用。
同步调用放在线程里跑（`asyncio.to_thread`），避免卡住事件循环。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("llm")


@dataclass(slots=True)
class LLMConfig:
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    # key 的三种来源：文件 > 环境变量 > 直接给值
    api_key_file: str = ""
    api_key_env: str = "DEEPSEEK_API_KEY"
    api_key: str = ""
    temperature: float = 0.9
    max_tokens: int = 900
    timeout: float = 60.0

    def resolve_key(self) -> str:
        if self.api_key:
            return self.api_key.strip()
        if self.api_key_file:
            p = Path(os.path.expanduser(self.api_key_file))
            try:
                return p.read_text(encoding="utf-8").strip()
            except Exception as e:
                log.warning("读不到 api_key_file（%s）：%r", p, e)
        return os.environ.get(self.api_key_env, "").strip()

    @classmethod
    def from_dict(cls, d: dict) -> LLMConfig:
        c = cls()
        c.base_url = str(d.get("base_url", c.base_url)).rstrip("/")
        c.model = str(d.get("model", c.model))
        c.api_key_file = str(d.get("api_key_file", ""))
        c.api_key_env = str(d.get("api_key_env", c.api_key_env))
        c.api_key = str(d.get("api_key", ""))
        c.temperature = float(d.get("temperature", c.temperature))
        c.max_tokens = int(d.get("max_tokens", c.max_tokens))
        c.timeout = float(d.get("timeout", c.timeout))
        return c


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    parse_error: bool = False


@dataclass(slots=True)
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _post(cfg: LLMConfig, body: dict[str, Any]) -> dict[str, Any]:
    key = cfg.resolve_key()
    if not key:
        raise RuntimeError("没有可用的 API key（检查 [llm] api_key_file / 环境变量）")
    req = urllib.request.Request(
        cfg.base_url + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore") or "{}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read()[:300].decode("utf-8", "ignore")
        except Exception:
            pass
        raise RuntimeError("HTTP %s：%s" % (e.code, detail)) from e


def chat(messages: list[dict[str, Any]], cfg: LLMConfig | None = None,
         max_tokens: int | None = None) -> str:
    """一次普通对话，返回文本。"""
    cfg = cfg or LLMConfig()
    d = _post(cfg, {"model": cfg.model, "messages": messages,
                    "temperature": cfg.temperature,
                    "max_tokens": int(max_tokens or cfg.max_tokens)})
    msg = ((d.get("choices") or [{}])[0].get("message") or {})
    txt = str(msg.get("content") or "").strip()
    if not txt:                      # 带思考的模型可能把答案放这儿
        txt = str(msg.get("reasoning_content") or "").strip()
    return txt


def chat_tools(messages: list[dict[str, Any]], tools: list[dict[str, Any]],
               cfg: LLMConfig | None = None,
               tool_choice: str | dict[str, Any] = "auto",
               max_tokens: int | None = None) -> LLMResponse:
    """带工具的一轮（等价于 OpenAI function calling）。"""
    cfg = cfg or LLMConfig()
    body: dict[str, Any] = {"model": cfg.model, "messages": messages,
                            "temperature": cfg.temperature,
                            "max_tokens": int(max_tokens or cfg.max_tokens)}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice or "auto"      # 可以是 "required" 或指定函数
    d = _post(cfg, body)
    ch = (d.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    calls: list[ToolCall] = []
    for tc in (msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments") or ""
        err = False
        try:
            args = json.loads(raw_args or "{}")
        except Exception:
            args, err = {}, True
        if not isinstance(args, dict):
            args = {"value": args}
        calls.append(ToolCall(id=str(tc.get("id") or ""), name=str(fn.get("name") or ""),
                              arguments=args, parse_error=err))
    return LLMResponse(content=str(msg.get("content") or ""), tool_calls=calls,
                       usage=d.get("usage") or {}, finish_reason=str(ch.get("finish_reason") or ""),
                       raw=d)
