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
# 用户那份放数据目录（部署更新的白名单永远不碰 data/）；仓库里只有 example
CUSTOM_MAPPING = os.path.join(DATA, "mapping__custom__.json")
LEGACY_MAPPING = os.path.join(BASE, "mapping.json")
EXAMPLE_MAPPING = os.path.join(BASE, "mapping.example.json")


def log(msg: str) -> None:
    sys.stderr.write("[personas] %s\n" % msg)
    sys.stderr.flush()


def ensure_custom_mapping() -> str:
    """第一次跑：把 example 复制成用户私有的一份（之后永不再动）。"""
    if os.path.exists(CUSTOM_MAPPING):
        return CUSTOM_MAPPING
    for src in (EXAMPLE_MAPPING, LEGACY_MAPPING):
        if os.path.exists(src):
            os.makedirs(DATA, exist_ok=True)
            with open(src, encoding="utf-8") as f:
                body = f.read()
            with open(CUSTOM_MAPPING, "w", encoding="utf-8") as f:
                f.write(body)
            log("生成了 %s（照着 example 的默认值，改这个文件）" % CUSTOM_MAPPING)
            return CUSTOM_MAPPING
    return ""


def load_mapping() -> dict:
    path = ensure_custom_mapping()
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log("读不到 %s：%r" % (path, e))
        return {}


def _read_dir_card(path: str) -> str:
    """目录卡：prompt.txt 作正文，其余 *.md 按固定顺序追加（memes → meme-mode → 其它）。"""
    if not os.path.isdir(path):
        return ""
    parts = []
    prompt = os.path.join(path, "prompt.txt")
    if os.path.exists(prompt):
        with open(prompt, encoding="utf-8") as f:
            parts.append(f.read().strip())
    preferred = ["memes.md", "meme-mode.md"]
    rest = sorted(n for n in os.listdir(path)
                  if n.endswith(".md") and n not in preferred)
    for n in preferred + rest:
        p = os.path.join(path, n)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                parts.append(f.read().strip())
    return "\n\n".join(p for p in parts if p)


def read_card(name: str) -> str:
    """先找运行时卡，再找仓库内置卡。目录卡与老的单文件卡都支持。"""
    if not name:
        return ""
    for base in (CARDS, BUILTIN_CARDS):
        path = os.path.join(base, name)
        if os.path.isdir(path):
            card = _read_dir_card(path)
            if card:
                return card
            continue
        try:
            with open(path, encoding="utf-8") as f:
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
    skip_common = name in (mp.get("append_common_except") or [])
    if mp.get("append_common") and not skip_common:
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
