#!/usr/bin/env python3
"""人格插件（personas）：按作用域挑人格卡，通过 get_context 交给核心。

协议：core/plugin/__init__.py
  hello      → {"name","version","tools":[],"capabilities":["context"]}
  get_context→ 入参 {"scope":{kind,id,key}, "sender":{id,name}}
               出参 {"persona": "...", "rules": [...], "knowledge": [...]}
"""

from __future__ import annotations

import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
CARDS = os.path.join(BASE, "cards")
MAPPING = os.path.join(BASE, "mapping.json")


def log(msg: str) -> None:
    sys.stderr.write("[personas] %s\n" % msg)
    sys.stderr.flush()


def load_mapping() -> dict:
    try:
        with open(MAPPING, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log("读不到 mapping.json：%r" % (e,))
        return {}


def read_card(name: str) -> str:
    if not name:
        return ""
    p = os.path.join(CARDS, name)
    try:
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        log("读不到卡片 %s" % name)
        return ""


def pick(scope: dict, mp: dict, channel: str = "") -> str:
    """优先级：具体群/具体人 > 通道种类 > default。"""
    kind = str(scope.get("kind") or "")
    sid = str(scope.get("id") or "")
    if kind == "group":
        hit = (mp.get("by_group") or {}).get(sid)
        if hit:
            return str(hit)
    elif kind == "private":
        hit = (mp.get("by_private") or {}).get(sid)
        if hit:
            return str(hit)
        if mp.get("private_default"):
            return str(mp["private_default"])
    # 通道种类兜底（cli / tcp / socket / onebot …）
    hit = (mp.get("by_channel") or {}).get(channel or "")
    if hit:
        return str(hit)
    if kind == "private" and mp.get("private_default"):
        return str(mp["private_default"])
    return str(mp.get("default") or "")


def get_context(params: dict) -> dict:
    mp = load_mapping()
    scope = params.get("scope") or {}
    channel = str((params.get("channel") or {}).get("name") or "")
    card = read_card(pick(scope, mp, channel))
    card = card.replace("{channel}", channel or "终端")
    parts = [card] if card else []
    if mp.get("append_common"):
        common = read_card("_common_patch.txt")
        if common:
            parts.append(common)
    persona = "\n\n".join(parts).strip()
    return {"persona": persona, "rules": [], "knowledge": []}


def handle(method: str, params: dict):
    if method == "hello":
        return {"name": "personas", "version": "2.1", "protocol": 1,
                "capabilities": ["context"], "events": [], "tools": []}
    if method == "ping":
        return {"pong": True}
    if method == "shutdown":
        return {"ok": True}
    if method == "get_context":
        return get_context(params)
    raise ValueError("不认识的方法：%s" % method)


def main() -> None:
    log("started")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        if str(msg.get("method") or "") == "event":
            continue
        mid = msg.get("id")
        try:
            send_ok = {"id": mid, "result": handle(str(msg.get("method") or ""),
                                                   msg.get("params") or {})}
        except Exception as e:
            send_ok = {"id": mid, "error": str(e)}
        sys.stdout.write(json.dumps(send_ok, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if str(msg.get("method") or "") == "shutdown":
            break


if __name__ == "__main__":
    main()
