"""本地小资料库（带索引）：把 data/kb/ 里的 md/txt 切块建索引，聊天时按相关度取几块注入。

- 索引：中文按二字切分 + 英文/数字按词切分，算 TF-IDF 稀疏向量，余弦相似度检索（纯本地，不花 token）
- 管理：往 data/kb/ 里丢 .md/.txt 就行，改完跑 `qqbot-kb build`（或者让插件自动重建：文件变新会自动重建）
- 只在相关度够高时才注入，且限制字数，避免污染上下文
"""
import json
import math
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "kb")
INDEX = os.path.join(DATA, "_index.json")
MAX_CHUNK = 420


def _tok(s: str) -> list:
    s = str(s or "").lower()
    zh = re.findall(r"[\u4e00-\u9fff]+", s)
    out = []
    for seg in zh:
        out += [seg[i:i + 2] for i in range(len(seg) - 1)] or [seg]
    # 英文/数字词：连字符与缩写都归一（AK-47 → ak-47 / ak47 / ak / 47 都进索引）
    for w in re.findall(r"[a-z0-9_]+(?:[-'+][a-z0-9_]+)*", s):
        out.append(w)
        if any(c in w for c in "-'+"):
            out.append(re.sub(r"[-'+]", "", w))
            out += [x for x in re.split(r"[-'+]", w) if len(x) >= 2]
    return out


def _files() -> list:
    out = []
    for root, _dirs, files in os.walk(DATA):
        for f in files:
            if f.startswith("_") or os.path.splitext(f)[1].lower() not in (".md", ".txt", ".markdown"):
                continue
            out.append(os.path.join(root, f))
    return sorted(out)


def _chunks(text: str, source: str) -> list:
    parts = re.split(r"\n\s*\n", str(text or ""))
    out, buf = [], ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(buf) + len(p) + 2 <= MAX_CHUNK:
            buf = (buf + "\n\n" + p) if buf else p
        else:
            if buf:
                out.append(buf)
            buf = p if len(p) <= MAX_CHUNK else p[:MAX_CHUNK]
    if buf:
        out.append(buf)
    return [{"source": source, "text": c} for c in out]


def build(verbose: bool = True) -> dict:
    chunks, df = [], {}
    for path in _files():
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for c in _chunks(text, os.path.relpath(path, DATA)):
            toks = _tok(c["text"])
            if not toks:
                continue
            tf = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            c["tf"] = tf
            chunks.append(c)
            for t in set(tf):
                df[t] = df.get(t, 0) + 1
    n = max(1, len(chunks))
    for c in chunks:
        vec = {}
        for t, v in c["tf"].items():
            idf = math.log((n + 1) / (df.get(t, 0) + 1)) + 1.0
            vec[t] = round((1 + math.log(v)) * idf, 4)
        norm = math.sqrt(sum(x * x for x in vec.values())) or 1.0
        c["vec"] = {k: v / norm for k, v in vec.items()}
        c.pop("tf", None)
    os.makedirs(DATA, exist_ok=True)
    tmp = INDEX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"built_at": int(time.time()), "files": len(_files()), "chunks": chunks},
                  f, ensure_ascii=False)
    os.replace(tmp, INDEX)
    info = {"files": len(_files()), "chunks": len(chunks)}
    if verbose:
        print("[kb] 建索引完成：%d 个文件 / %d 块" % (info["files"], info["chunks"]))
    return info


_cache = {"mtime": None, "data": None}


def _load() -> dict:
    try:
        mt = os.path.getmtime(INDEX)
    except Exception:
        return {"chunks": []}
    if _cache["mtime"] != mt:
        try:
            _cache["data"] = json.load(open(INDEX, encoding="utf-8"))
        except Exception:
            _cache["data"] = {"chunks": []}
        _cache["mtime"] = mt
    return _cache["data"] or {"chunks": []}


def search(query: str, top_k: int = 3, min_score: float = 0.14) -> list:
    """按相关度取几块资料。返回 [{source, text, score}]。"""
    toks = _tok(query)
    if not toks:
        return []
    d = _load()
    chunks = d.get("chunks") or []
    if not chunks:
        return []
    df = {}
    for c in chunks:
        for t in c.get("vec") or {}:
            df[t] = df.get(t, 0) + 1
    qv = {}
    for t in toks:
        qv[t] = qv.get(t, 0) + 1
    norm = math.sqrt(sum(v * v for v in qv.values())) or 1.0
    scored = []
    for c in chunks:
        s = 0.0
        best = 0.0
        for t, v in qv.items():
            tv = (c.get("vec") or {}).get(t)
            if tv:
                contrib = tv * (v / norm)
                s += contrib
                best = max(best, contrib)
        # 单个关键词强命中也算数：把"填充词稀释"补偿回来
        # （例："donk 是谁" 里 donk 命中就够；不然会被"是谁"拉低到阈值以下）
        s = max(s, best * math.sqrt(max(1, len(qv))))
        if s >= min_score:
            scored.append({"source": c.get("source"), "text": c.get("text"), "score": round(s, 3)})
    scored.sort(key=lambda x: -x["score"])
    return scored[: max(1, int(top_k or 3))]


def stats() -> dict:
    d = _load()
    return {"files": d.get("files"), "chunks": len(d.get("chunks") or []),
            "built_at": d.get("built_at"), "dir": DATA}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="本地小资料库")
    ap.add_argument("cmd", choices=["build", "search", "stats", "list"], nargs="?", default="stats")
    ap.add_argument("arg", nargs="?", default="")
    a = ap.parse_args()
    if a.cmd == "build":
        build()
        return 0
    if a.cmd == "search":
        for h in search(a.arg or "", 5):
            print("── [%s] 相关度 %s\n%s\n" % (h["source"], h["score"], h["text"][:200]))
        return 0
    if a.cmd == "list":
        for p in _files():
            print(" ", os.path.relpath(p, DATA), os.path.getsize(p), "字节")
        return 0
    print(stats())
    return 0


if __name__ == "__main__":
    sys.exit(main())
