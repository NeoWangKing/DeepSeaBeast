"""记忆子系统唯一的模型出口。

设计：只为「批处理」服务 —— 生成群印象/人物档案，和对话回复完全解耦。
以后换成免费模型（智谱/硅基流动/任何 OpenAI 兼容端点）只需要改 config.json
的 memory.provider.base_url / model / api_key_source，本文件不用动。
"""
import json
import os
import urllib.error
import urllib.request

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "api_key_source": "astrbot",   # astrbot=复用 AstrBot 里的 key；也可写 "env:名字" 或 "file:/路径" 或 "inline:xxxx"
    "timeout": 180,
    "temperature": 0.3,
    "max_tokens": 1500,
    # 思考型模型（如 GLM-5.3）用这两个参数；OpenAI 系模型留 None 即可
    "thinking": None,            # 例如 {"type": "enabled"}
    "reasoning_effort": None,    # low / high / max
}


def load_config() -> dict:
    try:
        with open(os.path.join(PLUGIN_DIR, "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def provider_cfg(cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    p = dict(DEFAULTS)
    p.update(((cfg.get("memory") or {}).get("provider") or {}))
    return p


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
    # 默认：复用 AstrBot 的 provider key（cmd_config.json）
    for path in ("/opt/astrbot/data/cmd_config.json",):
        try:
            c = json.load(open(path, encoding="utf-8-sig"))
        except Exception:
            continue
        for s in c.get("provider_sources", []):
            k = s.get("key") or s.get("api_key")
            if isinstance(k, list) and k:
                return str(k[0])
            if isinstance(k, str) and k:
                return k
    return ""


def chat(messages: list, cfg: dict | None = None, *, json_mode: bool = False, max_tokens: int | None = None) -> tuple:
    """调用模型。返回 (文本, usage字典)；失败抛异常，由调用方决定是否回滚。"""
    p = provider_cfg(cfg)
    key = _resolve_key(p.get("api_key_source"))
    if not key:
        raise RuntimeError("拿不到 API key（检查 memory.provider.api_key_source）")
    body = {
        "model": p.get("model"),
        "messages": messages,
        "temperature": p.get("temperature", 0.3),
        "max_tokens": max_tokens or p.get("max_tokens", 1500),
    }
    if p.get("thinking"):
        body["thinking"] = p["thinking"]
    if p.get("reasoning_effort"):
        body["reasoning_effort"] = p["reasoning_effort"]
    if json_mode:
        body["response_format"] = {"type": "json_object"}   # 免费模型若不支持，把 memory.provider.json_mode 设 false
    req = urllib.request.Request(
        str(p.get("base_url")).rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        d = json.load(urllib.request.urlopen(req, timeout=int(p.get("timeout", 180))))
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s: %s" % (e.code, e.read().decode()[:300]))
    u = d.get("usage") or {}
    text = (d.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    return text, u


def usage_note(u: dict) -> str:
    """真实 token 用量说明（永远记录，方便对额度）。"""
    u = u or {}
    return "输入 %d + 输出 %d = %d token" % (u.get("prompt_tokens", 0) or 0,
                                             u.get("completion_tokens", 0) or 0,
                                             u.get("total_tokens", 0) or
                                             ((u.get("prompt_tokens", 0) or 0) + (u.get("completion_tokens", 0) or 0)))


def estimate_cost(u: dict, cfg: dict | None = None) -> float:
    """只对 DeepSeek 计价（1元/M未命中 + 0.02/M命中 + 4元/M输出）；
    换成免费额度（智谱等）后返回 0，避免日志里出现假成本。"""
    p = provider_cfg(cfg)
    if "deepseek" not in str(p.get("base_url", "")).lower():
        return 0.0
    miss = (u.get("prompt_tokens", 0) or 0) - (u.get("prompt_cache_hit_tokens", 0) or 0)
    hit = u.get("prompt_cache_hit_tokens", 0) or 0
    out = u.get("completion_tokens", 0) or 0
    return (miss * 1.0 + hit * 0.02 + out * 4.0) / 1e6
