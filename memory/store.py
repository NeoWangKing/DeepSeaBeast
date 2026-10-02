"""记忆产物读写：文件格式、原子写、素材收集、证据校验。纯标准库。"""
import glob
import json
import os
import re
import time

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEM_DIR = os.path.join(PLUGIN_DIR, "data", "memory")
PROFILE_DIR = os.path.join(MEM_DIR, "group_profile")
MEMBERS_DIR = os.path.join(MEM_DIR, "members")
LOG_PATH = os.path.join(MEM_DIR, "run.log")

AUTO_BEGIN = "<!--AUTO-BEGIN"
AUTO_END = "<!--AUTO-END-->"


def ensure_dirs() -> None:
    for d in (MEM_DIR, PROFILE_DIR, MEMBERS_DIR):
        os.makedirs(d, exist_ok=True)


def log(msg: str) -> None:
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        ensure_dirs()
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# ---------------- 群印象 ----------------
def profile_path(gid: str) -> str:
    return os.path.join(PROFILE_DIR, "%s.md" % gid)


def read_profile(gid: str) -> str:
    """返回可直接注入的文本：自动区正文 + 手写区正文。
    跳过 HTML 注释、以 > 开头的元信息行、# 标题行、以及手写区的占位提示。"""
    try:
        with open(profile_path(gid), encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return ""
    auto, manual, where = [], [], "auto"
    for line in raw.splitlines():
        s2 = line.strip()
        if AUTO_END in s2:
            where = "manual"
            continue
        if s2.startswith("<!--") or not s2 or s2.startswith("#") or s2.startswith(">"):
            continue
        if s2.startswith("（可在这里写死规矩"):
            continue
        (auto if where == "auto" else manual).append(s2)
    return "\n".join(auto + manual).strip()


def write_profile(gid: str, body: str, meta: str = "") -> None:
    """整体替换自动区；手写区（AUTO-END 之后）保留。"""
    keep = ""
    try:
        with open(profile_path(gid), encoding="utf-8") as f:
            raw = f.read()
        if AUTO_END in raw:
            keep = raw.split(AUTO_END, 1)[1]
    except Exception:
        pass
    if not keep.strip():
        keep = "\n\n## 手写补充（不会被自动覆盖）\n（可在这里写死规矩，比如「这群人不喜欢被提学习」）\n"
    text = ("%s 由 memory/refresh.py 自动生成，每次整体替换 -->\n> %s\n%s\n%s%s"
            % (AUTO_BEGIN, meta, body.strip(), AUTO_END, keep))
    atomic_write(profile_path(gid), text)


# ---------------- 人物档案 ----------------
def members_path(gid: str) -> str:
    return os.path.join(MEMBERS_DIR, "%s.json" % gid)


def read_members(gid: str) -> dict:
    try:
        with open(members_path(gid), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"updated_at": 0, "members": {}}


def write_members(gid: str, data: dict) -> None:
    data["updated_at"] = int(time.time())
    atomic_write(members_path(gid), json.dumps(data, ensure_ascii=False, indent=1))


# ---------------- 素材 ----------------
def load_chatlog(gid: str, limit: int = 200) -> list:
    """读该群最近的聊天记录 [{t,u,n,x}]（chatlog 只记 incoming，不含她自己）。"""
    rows = []
    for f in sorted(glob.glob(os.path.join(PLUGIN_DIR, "chatlog", "*.jsonl"))):
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if str(d.get("g")) == str(gid) and (d.get("x") or "").strip():
                        rows.append(d)
        except Exception:
            continue
    rows.sort(key=lambda x: x.get("t", 0))
    return rows[-limit:]


def speech_volume(gid: str, since_ts: float = 0) -> int:
    """since_ts 之后该群新增了多少条消息（用于守卫条件）。"""
    n = 0
    for f in sorted(glob.glob(os.path.join(PLUGIN_DIR, "chatlog", "*.jsonl"))):
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if str(d.get("g")) == str(gid) and d.get("t", 0) > since_ts:
                        n += 1
        except Exception:
            continue
    return n


def fmt_lines(rows: list, max_len: int = 80) -> str:
    out = []
    for d in rows:
        who = (d.get("n") or d.get("u") or "?")[:12]
        txt = " ".join((d.get("x") or "").split())[:max_len]
        out.append("%s: %s" % (who, txt))
    return "\n".join(out)


# ---------------- 证据校验（借 note-slides 的"来源锚点"） ----------------
def _norm(s: str) -> str:
    return re.sub(r"[\s，。！？、,.!?~～…—\-—\"'“”‘’()（）\[\]【】]+", "", str(s or ""))


def evidence_ok(evidence: list, material: str) -> bool:
    """每条结论必须能在原始素材里找到出处，否则视为编造。"""
    hay = _norm(material)
    if not hay:
        return False
    for e in (evidence or []):
        ne = _norm(e)
        if len(ne) >= 6 and ne in hay:
            return True
        # 允许拆成两小段都在（防止模型改写标点）
        parts = [p for p in re.split(r"[，。！？、,.!?；;]", str(e or "")) if len(_norm(p)) >= 4]
        if parts and all(_norm(p) in hay for p in parts):
            return True
    return False


def trim_smart(body: str, limit: int) -> str:
    """按行/句边界裁剪，避免把话断在半截。"""
    body = (body or "").strip()
    if len(body) <= limit:
        return body
    keep = []
    for line in body.split("\n"):
        if not line.strip():
            continue
        if len("\n".join(keep + [line])) > limit:
            break
        keep.append(line)
    if keep:
        return "\n".join(keep)
    cut = body[:limit]
    for sep in ("。", "！", "？", "；"):
        i = cut.rfind(sep)
        if i > limit * 0.5:
            return cut[:i + 1]
    return cut.rstrip() + "…"


def parse_json(text: str) -> dict:
    """容忍模型输出的 ```json ``` 包裹与前后废话。"""
    t = (text or "").strip()
    if "```" in t:
        t = re.sub(r"```[a-zA-Z]*", "", t).replace("```", "").strip()
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        t = t[i:j + 1]
    return json.loads(t)
