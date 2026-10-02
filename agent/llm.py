"""Agent loop 用的 OpenAI 兼容工具调用客户端（一次性请求，不做循环）。

- 配置来源优先级：config.json 的 agent.llm > /opt/astrbot/data/cmd_config.json 里 deepseek 那条
- 只负责"发一次带 tools 的请求"，返回 content / tool_calls / usage
- 不 import astrbot，离线可测（tests/agent_loop_sim.py 用注入的假 chat_fn 跑循环）
"""
import json
import os
import urllib.request

DEFAULT_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
CMD_CONFIG = "/opt/astrbot/data/cmd_config.json"


def _resolve_key(src: str) -> str:
    src = str(src or "")
    if src.startswith("inline:"):
        return src[7:].strip()
    if src.startswith("env:"):
        return os.environ.get(src[4:], "").strip()
    if src.startswith("file:"):
        try:
            return open(src[5:].strip(), encoding="utf-8").read().strip()
        except Exception:
            return ""
    try:
        c = json.load(open(CMD_CONFIG, encoding="utf-8-sig"))
    except Exception:
        return ""
    for s in c.get("provider_sources", []):
        k = s.get("key") or s.get("api_key") or ""
        if isinstance(k, list):
            k = k[0] if k else ""
        b = str(s.get("api_base") or s.get("base_url") or "")
        if k and "deepseek" in b.lower():
            return str(k)
    for s in c.get("provider_sources", []):
        k = s.get("key") or s.get("api_key") or ""
        if isinstance(k, list):
            k = k[0] if k else ""
        if k:
            return str(k)
    return ""


def provider_cfg(cfg: dict = None) -> dict:
    """从插件 config.json 的 agent.llm 取配置，缺的用默认值补。"""
    c = dict((cfg or {}).get("agent", {}).get("llm") or {})
    c.setdefault("base_url", DEFAULT_BASE)
    c.setdefault("model", DEFAULT_MODEL)
    c.setdefault("api_key_source", "astrbot")
    c.setdefault("timeout", 60)
    c.setdefault("max_tokens", 900)
    c.setdefault("temperature", 0.9)
    c["api_key"] = _resolve_key(c.get("api_key_source"))
    return c


def chat_tools(messages, tools, tool_choice="required", cfg=None, max_tokens=None,
               timeout=None) -> dict:
    """发一次带工具的请求。返回 {content, tool_calls:[{id,name,arguments(dict)}], usage, raw}。"""
    p = provider_cfg(cfg)
    if not p.get("api_key"):
        raise RuntimeError("拿不到模型 key（agent.llm.api_key_source 没配好）")
    body = {
        "model": p["model"],
        "messages": messages,
        "temperature": p.get("temperature", 0.9),
        "max_tokens": int(max_tokens or p.get("max_tokens") or 900),
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice or "auto"
    req = urllib.request.Request(
        str(p["base_url"]).rstrip("/") + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + str(p["api_key"]),
                 "Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=float(timeout or p.get("timeout") or 60)))
    ch = (d.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    calls = []
    for tc in (msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {}
        if not isinstance(args, dict):
            args = {"value": args}
        calls.append({"id": tc.get("id") or "", "name": fn.get("name") or "", "arguments": args,
                      "raw": tc})
    return {"content": msg.get("content") or "", "tool_calls": calls,
            "usage": d.get("usage") or {}, "finish_reason": ch.get("finish_reason")}
