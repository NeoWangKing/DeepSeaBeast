#!/usr/bin/env python3
"""黑话 / 表达库插件：让她听得懂"这个群自己的说法"。

两样东西：
  terms    术语——某个词在这个群指什么（"玉湖" = 某个简称）
  patterns 句式——这个群的固定说法（"已完成今日{名词}大学习" = 打卡腔）

**命中才给**：只有当前这条消息里真的出现了，才把解释塞进上下文。
不命中就一个字都不给，免得每轮都塞一堆没用的背景。

数据：$DEEPSEA_PLUGIN_DATA/<scope>.json
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

DATA = os.environ.get("DEEPSEA_PLUGIN_DATA") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data")
MAX_LINES = 3          # 一次最多给几条解释
MAX_TERM = 24


def log(msg: str) -> None:
    sys.stderr.write("[jargon] %s\n" % msg)
    sys.stderr.flush()


def _path(scope: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(scope or "unknown"))[:64]
    return os.path.join(DATA, safe + ".json")


def load(scope: str) -> dict:
    os.makedirs(DATA, exist_ok=True)
    try:
        d = json.load(open(_path(scope), encoding="utf-8"))
    except Exception:
        d = {}
    d.setdefault("terms", [])
    d.setdefault("patterns", [])
    return d


def save(scope: str, d: dict) -> None:
    os.makedirs(DATA, exist_ok=True)
    p = _path(scope)
    tmp = p + ".tmp"
    d["updated_at"] = time.time()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, p)


def pattern_regex(surface: str) -> re.Pattern | None:
    """`已完成今日{名词}大学习` → 正则：占位符变成"任意几个字"。"""
    s = str(surface or "").strip()
    if not s:
        return None
    parts = re.split(r"\{[^}]*\}", s)
    if all(not p.strip() for p in parts):
        return None
    body = "[^，。！？\\s]{1,12}".join(re.escape(p) for p in parts)
    try:
        return re.compile(body)
    except re.error:
        return None


# ── 上下文注入（命中才给）────────────────────────────
def explain(scope: str, text: str) -> list[str]:
    if not text:
        return []
    d = load(scope)
    out: list[str] = []
    low = text.lower()

    # 术语：子串命中（长的优先，避免"鱼"命中"大肥鱼"这类）
    for t in sorted(d["terms"], key=lambda x: -len(x.get("term") or "")):
        term = str(t.get("term") or "").strip()
        if not term or term.lower() not in low:
            continue
        meaning = str(t.get("meaning") or "").strip()
        if meaning:
            out.append("「%s」在这个群里指：%s" % (term, meaning))
        if len(out) >= MAX_LINES:
            return out

    # 句式：把占位符当通配再匹配
    for p in d["patterns"]:
        rx = pattern_regex(p.get("surface") or "")
        if rx is None or not rx.search(text):
            continue
        note = str(p.get("note") or "").strip()
        if note:
            out.append("「%s」是这个群的固定说法：%s" % (p.get("surface"), note))
        if len(out) >= MAX_LINES:
            return out
    return out[:MAX_LINES]


# ── 工具 ──────────────────────────────────────────────
def do_learn_term(scope: str, args: dict) -> dict:
    term = str(args.get("term") or "").strip()[:MAX_TERM]
    meaning = str(args.get("meaning") or "").strip()[:120]
    if not term or not meaning:
        return {"ok": False, "error": "要说清词和它的意思"}
    d = load(scope)
    for t in d["terms"]:
        if t.get("term") == term:
            t["meaning"] = meaning
            t["updated"] = time.time()
            save(scope, d)
            return {"ok": True, "data": {"text": "更新了「%s」的意思" % term}}
    d["terms"].append({"term": term, "meaning": meaning,
                       "kind": str(args.get("kind") or "")[:12],
                       "created": time.time()})
    save(scope, d)
    return {"ok": True, "data": {"text": "记下了「%s」的意思" % term}}


def do_list_terms(scope: str, args: dict) -> dict:
    d = load(scope)
    rows = ["%s：%s" % (t.get("term"), t.get("meaning")) for t in d["terms"] if t.get("meaning")]
    pats = ["%s → %s" % (p.get("surface"), (p.get("note") or "")[:40]) for p in d["patterns"]]
    if not rows and not pats:
        return {"ok": True, "data": {"text": "这个群还没有记过什么说法"}}
    txt = []
    if rows:
        txt.append("术语（%d）：\n%s" % (len(rows), "\n".join(rows[:20])))
    if pats:
        txt.append("句式（%d）：\n%s" % (len(pats), "\n".join(pats[:20])))
    return {"ok": True, "data": {"text": "\n".join(txt)}}


def do_forget_term(scope: str, args: dict) -> dict:
    term = str(args.get("term") or "").strip()
    d = load(scope)
    before = len(d["terms"])
    d["terms"] = [t for t in d["terms"] if t.get("term") != term]
    save(scope, d)
    if len(d["terms"]) == before:
        return {"ok": False, "error": "没记过「%s」" % term}
    return {"ok": True, "data": {"text": "忘掉「%s」了" % term}}


TOOLS = [
    {"name": "learn_term", "label": "记群里的说法", "kind": "control",
     "description": "有人解释了群里某个说法/黑话是什么意思，就记下来（以后听到能听懂）。",
     "parameters": {"type": "object", "properties": {
         "term": {"type": "string", "description": "那个词或句式"},
         "meaning": {"type": "string", "description": "它在这个群里是什么意思"}},
         "required": ["term", "meaning"]}},
    {"name": "list_terms", "label": "看待记的说法", "kind": "query",
     "description": "看看这个群有哪些记下来的说法。",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "forget_term", "label": "忘掉说法", "kind": "control",
     "description": "记错或不用的说法删掉。",
     "parameters": {"type": "object", "properties": {"term": {"type": "string"}},
                    "required": ["term"]}},
]


def handle(method: str, params: dict):
    scope = str((params.get("scope") or {}).get("ref")
                or (params.get("scope") or {}).get("key") or "unknown")
    if method == "hello":
        log("started, data=%s" % DATA)
        return {"name": "jargon", "version": "1.0", "protocol": 1,
                "capabilities": ["context", "tools"], "events": [], "jobs": [], "tools": TOOLS}
    if method == "get_context":
        text = str(params.get("message") or "")
        return {"persona": "", "rules": [], "knowledge": explain(scope, text)}
    if method == "call_tool":
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        if name == "learn_term":
            return do_learn_term(scope, args)
        if name == "list_terms":
            return do_list_terms(scope, args)
        if name == "forget_term":
            return do_forget_term(scope, args)
        return {"ok": False, "error": "没有这个工具：%s" % name}
    if method == "ping":
        return {"pong": True}
    if method == "shutdown":
        return {"ok": True}
    raise ValueError("不认识的方法：%s" % method)


def main() -> None:
    os.makedirs(DATA, exist_ok=True)
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
            continue                      # 这个插件不订阅事件
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
