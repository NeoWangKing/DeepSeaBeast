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
COLLECT_ON = os.environ.get("STICKER_COLLECT", "1") not in ("0", "false", "False")
# 自动收集：群里别人发的贴图，收进来先"待打标"，不直接能用
COLLECT_MAX_BYTES = 2 * 1024 * 1024
COLLECT_PER_HOUR = 20


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


def nearest(query: str, n: int = 5) -> list[str]:
    """找不到时给几个相近的候选：按描述/标签的字符重叠排。"""
    idx = load_index()
    q = tokens(query)
    if not q:
        return []
    now = time.time()
    scored = []
    for it in idx["items"]:
        if not alive(it):
            continue
        base = tokens((it.get("desc") or "") + " " + " ".join(it.get("tags") or []))
        overlap = len(q & base)
        if overlap:
            scored.append((overlap + math.log1p(float(it.get("used") or 0)) * 0.3, it))
    if not scored:                      # 一个词都不重合，就给最常用的几张
        items = [it for it in idx["items"] if alive(it)]
        items.sort(key=lambda it: -int(it.get("used") or 0))
        return [short(it.get("desc"), 18) for it in items[:n]]
    scored.sort(key=lambda x: -x[0])
    return [short(it.get("desc"), 18) for _s, it in scored[:n]]


_RECENT: dict[str, list[dict]] = {}


def _recent_media(scope: str) -> list[dict]:
    return _RECENT.setdefault(scope, [])


def do_collect_last(scope: str, args: dict) -> dict:
    """把这个会话里最近的一张图收进表情包库（人说"这张收下"时用）。"""
    buf = [x for x in _recent_media(scope) if x.get("url")]
    if not buf:
        return {"ok": False, "error": "我这儿没有最近的图片记录"}
    idx = int(args.get("index") or 1)
    pickit = buf[-idx] if idx <= len(buf) else None
    if pickit is None:
        return {"ok": False, "error": "没有倒数第 %d 张图" % idx}
    msg = collect_sticker(pickit["url"], pickit.get("name") or "", force=True)
    return {"ok": "收进来" in msg, "data": {"text": msg}, "error": "" if "收进来" in msg else msg}


def _collect_state() -> dict:
    try:
        return json.load(open(os.path.join(DATA, "collect_state.json"), encoding="utf-8"))
    except Exception:
        return {}


def _collect_save(st: dict) -> None:
    try:
        tmp = os.path.join(DATA, "collect_state.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, os.path.join(DATA, "collect_state.json"))
    except Exception:
        pass


def _image_info(data: bytes) -> tuple[str, int, int]:
    """只看文件头判断格式和尺寸（GIF / PNG / JPEG），不依赖 Pillow。"""
    try:
        if data[:6] in (b"GIF87a", b"GIF89a"):
            w = int.from_bytes(data[6:8], "little")
            h = int.from_bytes(data[8:10], "little")
            return "gif", w, h
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            w = int.from_bytes(data[16:20], "big")
            h = int.from_bytes(data[20:24], "big")
            return "png", w, h
        if data[:2] == b"\xff\xd8":
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2):        # SOF0/1/2
                    h = int.from_bytes(data[i + 5:i + 7], "big")
                    w = int.from_bytes(data[i + 7:i + 9], "big")
                    return "jpg", w, h
                seg = int.from_bytes(data[i + 2:i + 4], "big")
                i += 2 + seg
    except Exception:
        pass
    return "?", 0, 0


def looks_like_sticker(data: bytes) -> tuple[bool, str]:
    """猜这张图是不是表情包。

    为什么靠猜：SnowLuma 把 QQ 表情包降级成了普通 image 段（实测原始数据只有
    file/url 两个字段，没有 summary / sub_type / emoji_id），协议上分不出来。
    判据只求"宁可漏收，不要错收"——收错了也只是躺在待打标里，不会发出去。
    """
    fmt, w, h = _image_info(data)
    if fmt == "gif":
        return True, "动图"
    if not w or not h:
        return False, ""
    ratio = w / float(h)
    if 0.72 <= ratio <= 1.4 and min(w, h) <= 320 and len(data) <= 512 * 1024:
        return True, "小方图 %dx%d" % (w, h)
    return False, "%dx%d" % (w, h)


def collect_sticker(url: str, name: str = "", force: bool = False) -> str:
    """下载别人发的贴图存进库里（标 pending，没打标之前用不上）。

    返回一句话说明结果，给日志用。
    """
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "没有可下载的地址"
    now = time.time()
    st = _collect_state()
    hour = int(now // 3600)
    if int(st.get("hour") or 0) != hour:
        st = {"hour": hour, "count": 0, "md5": st.get("md5") or []}
    if int(st.get("count") or 0) >= COLLECT_PER_HOUR:
        return "本小时收够了（%d 张）" % COLLECT_PER_HOUR
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "DeepSeaBeast/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read(COLLECT_MAX_BYTES + 1)
    except Exception as e:
        return "下载失败：%r" % (e,)
    if len(data) > COLLECT_MAX_BYTES:
        return "图太大（>%d KB）" % (COLLECT_MAX_BYTES // 1024)
    if not force:
        ok, why = looks_like_sticker(data)
        if not ok:
            return "看着不像表情包（%s），跳过" % (why or "尺寸不符")
    import hashlib
    md5 = hashlib.md5(data).hexdigest()
    if md5 in (st.get("md5") or []):
        return "已经有了"
    idx = load_index()
    if any(it.get("md5") == md5 for it in idx["items"]):
        return "库里已有"
    ext = "gif" if data[:6] in (b"GIF87a", b"GIF89a") else (
        "png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "jpg")
    sid = "c%d" % int(now * 1000)
    fn = "%s.%s" % (sid, ext)
    _ensure()
    with open(os.path.join(LIB, fn), "wb") as f:
        f.write(data)
    idx["items"].append({"id": sid, "file": fn, "desc": "", "tags": [],
                         "name": str(name or "")[:40], "md5": md5,
                         "size": len(data), "group": "", "added_at": now,
                         "used": 0, "last_used": 0, "status": "pending"})
    save_index(idx)
    st["count"] = int(st.get("count") or 0) + 1
    st.setdefault("md5", []).append(md5)
    st["md5"] = st["md5"][-200:]
    _collect_save(st)
    return "收进来了：%s" % fn


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
        near = nearest(query, 5)
        if near:
            return {"ok": False, "error": "没找到「%s」的贴图。相近的有：%s（换个说法再试，或者用 list_stickers 看全部）"
                    % (query, "、".join(near))}
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
    rows = []
    for it in items[:MAX_LIST]:
        if not it.get("desc"):
            rows.append("%s  【待打标】%s" % (it.get("id"), it.get("name") or it.get("file")))
        else:
            rows.append("%s  %s  #%s" % (it.get("id"), short(it.get("desc"), 30),
                                         "/".join((it.get("tags") or [])[:3])))
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
    if target.get("desc"):
        target["status"] = "ok"          # 打过标就能用了
    save_index(idx)
    return {"ok": True, "data": {"text": "已更新 %s" % sid}}


TOOLS = [
    {"name": "send_sticker", "description": "发一张**表情包/贴图**（一张图），按情绪挑，"
                                          "例如「笑死」「无语」「点赞」。"
                                          "**不是小表情**——对方只说「表情」时用 send_face。",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "想要的情绪或意思，用中文短语"}},
         "required": ["query"]},
     "requires": ["sticker"], "label": "发贴图", "kind": "action"},
    {"name": "list_stickers", "description": "看看有哪些表情包可用（可按关键词过滤）",
     "parameters": {"type": "object", "properties": {
         "keyword": {"type": "string", "description": "可选关键词"}}},
     "requires": ["sticker"], "label": "看贴图", "kind": "query"},
    {"name": "note_sticker", "description": "改某张表情包的备注或分类标签（管理用）",
     "parameters": {"type": "object", "properties": {
         "id": {"type": "string"},
         "desc": {"type": "string", "description": "新的描述"},
         "tags": {"type": "array", "items": {"type": "string"}, "description": "新的标签列表"}},
         "required": ["id"]},
     "requires": ["sticker"], "label": "改贴图备注", "kind": "control"},
]


def on_event(event: str, data: dict) -> None:
    """群里有人发贴图 → 收进库（待打标，打标前她发不出去）。"""
    if event != "message.received":
        return
    if not COLLECT_ON:
        return
    scope = scope_of(data)
    for m in (data.get("media") or []):
        url = str(m.get("url") or "")
        if not url:
            continue
        kind = str(m.get("kind") or "")
        # 最近一张图记下来：她说"这张收下"时能精确收
        _recent_media(scope)[:0] = []
        buf = _recent_media(scope)
        buf.append({"url": url, "kind": kind, "name": str(m.get("name") or ""),
                    "ts": time.time()})
        del buf[:-8]
        if kind == "sticker":
            log("收集贴图：%s" % collect_sticker(url, str(m.get("name") or ""), force=True))
        elif kind == "image":
            log("看到图片：%s" % collect_sticker(url, "", force=False))


def scope_of(params: dict) -> str:
    """会话引用：工具调用和事件里的 scope 结构一样。"""
    sc = params.get("scope") or {}
    return str(sc.get("ref") or sc.get("key") or "unknown")


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
        if name == "collect_last_image":
            return do_collect_last(scope_of(params), args)
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
