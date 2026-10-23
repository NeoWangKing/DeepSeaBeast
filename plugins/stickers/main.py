#!/usr/bin/env python3
"""表情包插件：管"贴图"这一件事的全部——库存、备注、分类、挑选、发送。

**贴图不等于图片**：核心那边是独立的 `sticker` 动词，
插件只负责"挑哪张、这张叫啥"，真正怎么送到对面是通道的事。

数据（都在 $DEEPSEA_PLUGIN_DATA）：
  library/     图片本体（gif/png/jpg/webp）
  index.json   {"items": [{id, file, desc, tags, group, used, last_used, ...}]}

工具：
  send_sticker(query)            挑一张发出去
  list_stickers(keyword)         看有哪些（可按关键词过滤）
  note_sticker(id, desc, tags)   改备注/分类
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time

DATA = os.environ.get("DEEPSEA_PLUGIN_DATA") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data")
LIB = os.path.join(DATA, "library")
INDEX = os.path.join(DATA, "index.json")

MAX_LIST = 40


def log(msg: str) -> None:
    sys.stderr.write("[stickers] %s\n" % msg)
    sys.stderr.flush()


def _ensure() -> None:
    os.makedirs(LIB, exist_ok=True)


def load_index() -> dict:
    _ensure()
    try:
        with open(INDEX, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault("items", [])
    return d


def save_index(d: dict) -> None:
    _ensure()
    d["updated_at"] = time.time()
    tmp = INDEX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, INDEX)


def item_path(it: dict) -> str:
    return os.path.join(LIB, str(it.get("file") or ""))


def alive(it: dict) -> bool:
    return bool(it.get("file")) and os.path.isfile(item_path(it))


def tokens(text: str) -> set[str]:
    t = str(text or "").lower()
    out = set(re.findall(r"[a-z0-9]{2,}", t))
    cjk = re.findall(r"[\u4e00-\u9fff]", t)
    out |= {"".join(cjk[i:i + 2]) for i in range(len(cjk) - 1)}
    return out


def score(it: dict, q: set[str], now: float) -> float:
    if not q:
        return 1.0
    tag_hit = len(q & tokens(" ".join(it.get("tags") or [])))
    desc_hit = len(q & tokens(it.get("desc") or ""))
    if not (tag_hit or desc_hit):
        return 0.0
    age_days = max(0.0, (now - float(it.get("last_used") or it.get("added_at") or 0)) / 86400)
    return tag_hit * 3 + desc_hit * 2 + math.log1p(float(it.get("used") or 0)) * 0.5 \
        - min(age_days, 60) * 0.02


def pick(query: str, group: str = "") -> dict | None:
    idx = load_index()
    items = [it for it in idx["items"] if alive(it)]
    if not items:
        return None
    q = tokens(query)
    now = time.time()
    scored = [(score(it, q, now), it) for it in items]
    scored = [(s, it) for s, it in scored if s > 0]
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def mark_used(it: dict) -> None:
    idx = load_index()
    for x in idx["items"]:
        if x.get("id") == it.get("id"):
            x["used"] = int(x.get("used") or 0) + 1
            x["last_used"] = time.time()
            break
    save_index(idx)


def short(desc: str, n: int = 24) -> str:
    d = str(desc or "").strip()
    if not d:
        return "贴图"
    head = re.split(r"[：:，,（(]", d)[0].strip()
    return (head or d)[:n]


# ── 工具 ──────────────────────────────────────────────
def do_send_sticker(args: dict) -> dict:
    query = str(args.get("query") or args.get("name") or "").strip()
    it = pick(query)
    if it is None:
        return {"ok": False, "error": "没找到合适的贴图（库里没有匹配 %r 的）" % query}
    mark_used(it)
    name = short(it.get("desc"))
    return {"ok": True, "data": {"send": [
        {"kind": "sticker", "id": it.get("id"), "path": item_path(it), "name": name}]}}


def do_list_stickers(args: dict) -> dict:
    kw = str(args.get("keyword") or "").strip()
    idx = load_index()
    items = [it for it in idx["items"] if alive(it)]
    if kw:
        q = tokens(kw)
        items = [it for it in items if score(it, q, time.time()) > 0]
    items.sort(key=lambda it: -int(it.get("used") or 0))
    rows = ["%s  %s  #%s" % (it.get("id"), short(it.get("desc"), 30),
                             "/".join((it.get("tags") or [])[:3])) for it in items[:MAX_LIST]]
    if not rows:
        return {"ok": True, "data": {"text": "没有匹配的贴图"}}
    return {"ok": True, "data": {"text": "贴图（%d 张）：\n%s" % (len(items), "\n".join(rows))}}


def do_note_sticker(args: dict) -> dict:
    sid = str(args.get("id") or "").strip()
    idx = load_index()
    target = next((it for it in idx["items"] if it.get("id") == sid), None)
    if target is None:
        return {"ok": False, "error": "没有这张贴图：%s" % sid}
    if args.get("desc"):
        target["desc"] = str(args["desc"])[:200]
    if args.get("tags"):
        tags = args["tags"]
        target["tags"] = [str(t)[:12] for t in (tags if isinstance(tags, list) else [tags])][:8]
    target["updated"] = time.time()
    save_index(idx)
    return {"ok": True, "data": {"text": "已更新 %s" % sid}}


TOOLS = [
    {"name": "send_sticker", "description": "发一张表情包（贴图），按情绪/意思挑，例如「笑死」「无语」「点赞」",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "想要的情绪或意思，用中文短语"}},
         "required": ["query"]},
     "requires": ["sticker"]},
    {"name": "list_stickers", "description": "看看有哪些表情包可用（可按关键词过滤）",
     "parameters": {"type": "object", "properties": {
         "keyword": {"type": "string", "description": "可选关键词"}}},
     "requires": ["sticker"]},
    {"name": "note_sticker", "description": "改某张表情包的备注或分类标签（管理用）",
     "parameters": {"type": "object", "properties": {
         "id": {"type": "string"},
         "desc": {"type": "string", "description": "新的描述"},
         "tags": {"type": "array", "items": {"type": "string"}, "description": "新的标签列表"}},
         "required": ["id"]},
     "requires": ["sticker"]},
]


def on_event(event: str, data: dict) -> None:
    """群里有人发贴图：先只记日志，以后可以按群统计偏好 / 自动收进库。"""
    if event != "message.received":
        return
    name = str(data.get("sticker_name") or "")
    if name:
        log("收到贴图：%s（%s）" % (name, (data.get("scope") or {}).get("key")))


def handle(method: str, params: dict):
    if method == "hello":
        idx = load_index()
        log("库存 %d 张，目录 %s" % (len(idx["items"]), LIB))
        return {"name": "stickers", "version": "1.0", "protocol": 1,
                "capabilities": ["tools", "events"], "events": ["message.received"],
                "jobs": [], "tools": TOOLS}
    if method == "call_tool":
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        if name == "send_sticker":
            return do_send_sticker(args)
        if name == "list_stickers":
            return do_list_stickers(args)
        if name == "note_sticker":
            return do_note_sticker(args)
        return {"ok": False, "error": "没有这个工具：%s" % name}
    if method == "ping":
        return {"pong": True}
    if method == "shutdown":
        return {"ok": True}
    raise ValueError("不认识的方法：%s" % method)


def main() -> None:
    _ensure()
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
            p = msg.get("params") or {}
            try:
                on_event(str(p.get("event") or ""), p.get("data") or {})
            except Exception as e:
                log("事件处理失败：%r" % (e,))
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
