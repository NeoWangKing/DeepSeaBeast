"""插件侧的最小 LLM 客户端（只依赖标准库）。

配置由核心在 hello 里给：base_url / model / api_key_file（或 api_key_env）。
**不接收密钥本身**，只接收"去哪儿读"。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class LLM:
    def __init__(self, cfg: dict) -> None:
        self.base_url = str(cfg.get("base_url") or "").rstrip("/")
        self.model = str(cfg.get("model") or "")
        self.api_key_file = str(cfg.get("api_key_file") or "")
        self.api_key_env = str(cfg.get("api_key_env") or "DEEPSEEK_API_KEY")
        self.timeout = float(cfg.get("timeout") or 60)
        self.last_usage: dict = {}

    def _key(self) -> str:
        if self.api_key_file:
            try:
                with open(os.path.expanduser(self.api_key_file), encoding="utf-8") as f:
                    return f.read().strip()
            except Exception:
                pass
        return os.environ.get(self.api_key_env, "").strip()

    @property
    def ready(self) -> bool:
        return bool(self.base_url and self.model and self._key())

    def chat(self, messages: list[dict], max_tokens: int = 800,
             temperature: float = 0.3) -> str:
        key = self._key()
        if not key:
            raise RuntimeError("没有可用的模型密钥")
        body = {"model": self.model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens}
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode(),
            method="POST",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8", "ignore") or "{}")
        self.last_usage = data.get("usage") or {}
        msg = ((data.get("choices") or [{}])[0].get("message") or {})
        return str(msg.get("content") or "").strip()
