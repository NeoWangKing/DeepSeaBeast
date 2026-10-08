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


DOC_EXTS = (".md", ".txt", ".markdown")
CODE_EXTS = (".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml", ".ini",
             ".cfg", ".conf", ".go", ".rs", ".java", ".kt", ".kts", ".c", ".h", ".cpp", ".hpp",
             ".cs", ".rb", ".php", ".sh", ".bash", ".sql", ".html", ".css", ".scss", ".vue",
             ".svelte", ".gradle", ".properties", ".env", ".proto", ".lua", ".swift", ".mm",
             ".dart", ".nix", ".zig", ".mjs", ".cjs", ".ex", ".exs", ".erl", ".hs", ".scala",
             ".groovy", ".pl", ".r", ".jl", ".tf", ".hcl", ".cmake", ".mk", ".bzl", ".mdx",
             ".rst", ".adoc", ".json5", ".jsonc", ".tex", ".vim", ".el", ".fish", ".ps1",
             ".bat", ".cmd", ".asm", ".s", ".v", ".sv", ".vhd")
# 明确是二进制的后缀（导入仓库时按"不是二进制就收"来筛，免得漏掉冷门语言）
BINARY_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svgz", ".pdf",
               ".zip", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".7z", ".rar", ".tar",
               ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".ogg", ".wav", ".flac",
               ".mp4", ".webm", ".mov", ".avi", ".mkv", ".so", ".dylib", ".dll", ".exe",
               ".bin", ".o", ".a", ".rlib", ".class", ".jar", ".pyc", ".pyo", ".wasm",
               ".ttc", ".pfb", ".psd", ".ai", ".sketch", ".blend", ".fbx", ".glb", ".db",
               ".sqlite", ".sqlite3", ".lock", ".snap", ".img", ".iso", ".deb", ".rpm")
SKIP_DIRS = (".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea",
             ".vscode", "target", "vendor", "site-packages")


def scope_dir(scope: str = "") -> str:
    """域目录：scope 为空 → 默认域（data/kb 本身）。"""
    s = str(scope or "").strip()
    return os.path.join(DATA, s) if s else DATA


def is_scope_dir(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "_scope.json"))


def _index_path(scope: str = "") -> str:
    """默认域用老的 data/kb/_index.json；子域把索引放自己目录里。"""
    s = str(scope or "").strip()
    return os.path.join(scope_dir(s), "_index.json") if s else INDEX


def _files(scope: str = "") -> list:
    """列出该域要索引的文件。默认域跳过所有子域目录，避免混在一起。"""
    s = str(scope or "").strip()
    root = scope_dir(s)
    out = []
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs
                   if d not in SKIP_DIRS and not (not s and is_scope_dir(os.path.join(cur, d)))]
        for f in files:
            if f.startswith("_") or f.lower() in ("license", "licenses", "notice"):
                continue
            ext = os.path.splitext(f)[1].lower()
            if s:
                # 子域（仓库）：不是二进制就收 —— 免得仓库主语言不在白名单时导进去是空的
                if ext in BINARY_EXTS:
                    continue
            else:
                # 默认域保持老规则：只收文档类
                if ext not in DOC_EXTS and f.lower() not in ("dockerfile", "makefile", "readme"):
                    continue
            out.append(os.path.join(cur, f))
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


def build(scope: str = "", verbose: bool = True) -> dict:
    chunks, df = [], {}
    for path in _files(scope):
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
    _idx = _index_path(scope)
    os.makedirs(os.path.dirname(_idx), exist_ok=True)
    tmp = _idx + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"built_at": int(time.time()), "files": len(_files(scope)), "chunks": chunks},
                  f, ensure_ascii=False)
    os.replace(tmp, _idx)
    info = {"files": len(_files(scope)), "chunks": len(chunks)}
    if verbose:
        print("[kb] 建索引完成：%d 个文件 / %d 块" % (info["files"], info["chunks"]))
    return info


_cache = {}          # 按域缓存：{scope: {mtime, data}}


def _load(scope: str = "") -> dict:
    key = str(scope or "")
    try:
        mt = os.path.getmtime(_index_path(key))
    except Exception:
        return {"chunks": []}
    slot = _cache.get(key) or {}
    if slot.get("mtime") != mt:
        try:
            data = json.load(open(_index_path(key), encoding="utf-8"))
        except Exception:
            data = {"chunks": []}
        slot = {"mtime": mt, "data": data}
        _cache[key] = slot
    return slot.get("data") or {"chunks": []}


_STOP_TOPIC = {"md", "news", "cs", "the", "and", "的", "了"}


def _file_topic(source: str, text: str) -> set:
    """文件的"主题词"：文件名 + 第一行标题里的中文/英文实体词。"""
    out = set()
    base = os.path.splitext(os.path.basename(str(source)))[0]
    for w in re.split(r"[-_/]+", base):
        w = w.strip().lower()
        if len(w) >= 3 and w not in _STOP_TOPIC:
            out.add(w)
    head = str(text or "").splitlines()[0] if str(text or "").strip() else ""
    for w in re.findall(r"[\u4e00-\u9fff]{2,6}", head):
        out.add(w)
    for w in re.findall(r"[A-Za-z]{2,10}", head):
        out.add(w.lower())
    return out


def search(query: str, top_k: int = 3, min_score: float = 0.08, scope: str = "") -> list:
    """按相关度取几块资料。返回 [{source, text, score}]。"""
    toks = _tok(query)
    # 疑问/泛化词不参与打分：不然"原神圣遗物怎么刷"会被含"怎么"的其他资料挤掉
    _FILLER = {"怎么", "什么", "为什么", "如何", "是不是", "哪里", "哪个", "多少", "多少钱",
               "介绍", "玩法", "意思", "是什么", "有没有", "可以", "需要", "怎么刷", "么刷"}
    _keep = [t for t in toks if t not in _FILLER]
    if _keep:
        toks = _keep
    if not toks:
        return []
    d = _load(scope)
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
    # 查询里的连续中文片段（≥2 字）：在块里"整段出现"说明强相关，额外加分
    phrases = []
    for _run in re.findall(r"[\u4e00-\u9fff]{2,}", str(query)):
        for _L in range(min(len(_run), 8), 1, -1):        # 长短语优先
            for _i in range(0, len(_run) - _L + 1):
                _sub = _run[_i:_i + _L]
                if _sub not in phrases:
                    phrases.append(_sub)
        if len(phrases) > 80:
            break
    norm = math.sqrt(sum(v * v for v in qv.values())) or 1.0
    # 主题过滤：查询里出现了某文件的"主题词"（如 明日方舟 / 原神 / cs2）→ 只保留主题相关的文件
    _topics = {}
    for c in chunks:
        src = c.get("source")
        if src not in _topics:
            _topics[src] = _file_topic(src, c.get("text"))
    _ql = str(query).lower()
    _hit_topics = set()
    for src, ts in _topics.items():
        for t in ts:
            if len(t) >= 2 and t in _ql:
                _hit_topics.add(t)
    _allowed = None
    if _hit_topics:
        _allowed = {src for src, ts in _topics.items() if ts & _hit_topics}
        if not _allowed:
            _allowed = None

    scored = []
    for c in chunks:
        if _allowed is not None and c.get("source") not in _allowed:
            continue
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
        _rare = [t for t in qv if df.get(t, 9) <= 3]
        if _rare and any((c.get("vec") or {}).get(t) for t in _rare):
            s = max(s, best * math.sqrt(max(1, len(qv))))
        _text = str(c.get("text") or "")
        _bonus = 0.0
        for _ph in phrases:
            if _ph in _text:
                _bonus += 0.05 * min(3.0, len(_ph) / 2.0)
        s += min(_bonus, 0.25)
        if s >= min_score:
            scored.append({"source": c.get("source"), "text": _text, "score": round(s, 3)})
    scored.sort(key=lambda x: -x["score"])
    # 同一个文件最多占 2 块（让不同资料都有机会被注入）
    out, per = [], {}
    for it in scored:
        src = it.get("source")
        if per.get(src, 0) >= 2:
            continue
        out.append(it)
        per[src] = per.get(src, 0) + 1
        if len(out) >= max(1, int(top_k or 3)):
            break
    return out


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


def build_all(verbose: bool = True) -> dict:
    """重建默认域 + 所有子域索引（子域 = 带 _scope.json 的目录）。"""
    out = {}
    try:
        out["default"] = build("", verbose)
    except Exception as e:
        out["default"] = {"error": str(e)[:80]}
    try:
        subs = [d for d in sorted(os.listdir(DATA))
                if os.path.isdir(os.path.join(DATA, d)) and is_scope_dir(os.path.join(DATA, d))]
    except Exception:
        subs = []
    for s in subs:
        try:
            out[s] = build(s, verbose)
        except Exception as e:
            out[s] = {"error": str(e)[:80]}
    if verbose:
        print("[kb] 全部域重建完成：%s" % out)
    return out
