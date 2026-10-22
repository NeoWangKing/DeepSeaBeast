#!/usr/bin/env python3
"""人格插件（personas）：按"具体群/具体人 > 通道种类 > 自动建档"选人格卡。

分工：
  adapter 只说"这是哪儿、这地方是什么类别、叫什么名字"（scope_info）
  本插件负责"怎么存档、存哪儿、卡里写什么"（mapping.json 里的 filing 模板）

卡库两级：
  运行时（可写）  $DEEPSEA_PLUGIN_DATA/cards/   自动建档写这里，手工也能改
  内置（只读）    plugins/personas/cards/       仓库里的种子卡
查找顺序：运行时 → 内置 → 按模板自动建档

协议见 docs/PLUGIN_API.md
"""

from __future__ import annotations

import datetime
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
BUILTIN_CARDS = os.path.join(BASE, "cards")
DATA = os.environ.get("DEEPSEA_PLUGIN_DATA") or os.path.join(BASE, "data")
CARDS = os.path.join(DATA, "cards")
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
    """先找运行时卡，再找仓库内置卡。"""
    if not name:
        return ""
    for base in (CARDS, BUILTIN_CARDS):
        try:
            with open(os.path.join(base, name), encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            continue
    log("读不到卡片 %s" % name)
    return ""


def fill(text: str, scope: dict, channel: str) -> str:
    return (text.replace("{label}", str(scope.get("label") or scope.get("kind") or ""))
                .replace("{name}", str(scope.get("name") or ""))
                .replace("{id}", str(scope.get("id") or ""))
                .replace("{channel}", channel or "")
                .replace("{date}", datetime.date.today().isoformat()))


def pick(scope: dict, mp: dict, channel: str = "") -> str:
    """返回卡名；返回空串表示"这里还没建档"，交给 ensure_card。"""
    kind = str(scope.get("kind") or "")
    sid = str(scope.get("id") or "")
    if kind == "group":
        return str((mp.get("by_group") or {}).get(sid) or "")
    if kind == "private":
        return str((mp.get("by_private") or {}).get(sid)
                   or mp.get("private_default") or "")
    return str((mp.get("by_channel") or {}).get(channel or "") or "")


def ensure_card(mp: dict, scope: dict, channel: str) -> str:
    """没见过的环境 → 按 filing 模板自动建档，返回相对路径。"""
    kind = str(scope.get("kind") or "local")
    filing = (mp.get("filing") or {}).get(kind) or "channels/{channel}.md"
    rel = fill(filing, scope, channel)
    path = os.path.join(CARDS, rel)
    if os.path.exists(path):
        return rel
    tpl = read_card(mp.get("template") or "_template.txt") or "[{label} {name}]"
    body = fill(tpl, scope, channel)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body.rstrip() + "\n")
        os.replace(tmp, path)
        log("自动建档：%s" % rel)
    except Exception as e:
        log("建档失败 %s：%r" % (rel, e))
        return ""
    return rel


def get_context(params: dict) -> dict:
    mp = load_mapping()
    scope = dict(params.get("scope") or {})
    channel = str((params.get("channel") or {}).get("name") or "")

    name = pick(scope, mp, channel)
    if not name:
        name = ensure_card(mp, scope, channel) or str(mp.get("default") or "")

    card = fill(read_card(name), scope, channel)
    parts = [card] if card else []
    if mp.get("append_common"):
        common = read_card("_common_patch.txt")
        if common:
            parts.append(common)
    return {"persona": "\n\n".join(parts).strip(), "rules": [], "knowledge": []}


def handle(method: str, params: dict):
    if method == "hello":
        return {"name": "personas", "version": "3.0", "protocol": 1,
                "capabilities": ["context"], "events": [], "tools": [], "jobs": []}
    if method == "ping":
        return {"pong": True}
    if method == "shutdown":
        return {"ok": True}
    if method == "get_context":
        return get_context(params)
    raise ValueError("不认识的方法：%s" % method)


def main() -> None:
    log("started, data=%s" % DATA)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = str(msg.get("method") or "")
        if method == "event":
            continue
        try:
            out = {"id": msg.get("id"), "result": handle(method, msg.get("params") or {})}
        except Exception as e:
            out = {"id": msg.get("id"), "error": str(e)}
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if method == "shutdown":
            break


if __name__ == "__main__":
    main()
