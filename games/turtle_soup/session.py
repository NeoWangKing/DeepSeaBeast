"""海龟汤一局的状态：存在 data/games/turtle_soup/<会话id>.json（跑在内存里，进程重启也可恢复）。"""
import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(os.path.dirname(HERE))
DATA_DIR = os.path.join(PLUGIN_DIR, "data", "games", "turtle_soup")


def _safe(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z_\-]", "_", str(name or "unknown"))[:80]


def path(key: str) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, _safe(key) + ".json")


_MEM = {}          # 隐私群用：一局状态只存在内存里，游戏结束即消失、永不落盘


def load(key: str, no_disk: bool = False) -> dict:
    if no_disk:
        d = _MEM.get(key) or {}
        return d if d.get("active") else {}
    try:
        with open(path(key), encoding="utf-8") as f:
            d = json.load(f)
        if d.get("active"):
            return d
    except Exception:
        pass
    return {}


def save(key: str, d: dict, no_disk: bool = False) -> None:
    if no_disk:
        _MEM[key] = dict(d)
        return
    try:
        p = path(key)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except Exception:
        pass


def start(key: str, puzzle: dict, no_disk: bool = False) -> dict:
    d = {
        "active": True,
        "puzzle_id": puzzle.get("id"),
        "title": puzzle.get("title", ""),
        "category": puzzle.get("category", ""),
        "started_at": int(time.time()),
        "asked": 0,
        "facts": [],
        "qa": [],            # 只留最近几条，避免上下文膨胀
        "players": [],
    }
    save(key, d, no_disk)
    return d


def finish(key: str, puzzle_id: str, no_disk: bool = False) -> None:
    """一局结束：留一条"余温"记录（不 active），供玩家事后追问汤底。

    把整局记录（含 log 里的全部问答）一起留着 —— 事后玩家质疑"你前面为什么答不是"时，
    要能看到当时到底答了什么，才可能认错并给对解释。
    """
    if no_disk:
        d = dict(_MEM.get(key) or {})
    else:
        try:
            d = json.load(open(path(key), encoding="utf-8"))
        except Exception:
            d = {}
    d = dict(d or {})
    d["active"] = False
    d["puzzle_id"] = puzzle_id or d.get("puzzle_id")
    d["finished_at"] = int(time.time())
    save(key, d, no_disk)


def load_finished(key: str, within_sec: int = 1800, no_disk: bool = False) -> dict:
    """取最近刚结束的那局（超过 within_sec 视为过期）。"""
    if no_disk:
        d = _MEM.get(key) or {}
    else:
        try:
            d = json.load(open(path(key), encoding="utf-8"))
        except Exception:
            return {}
    if d and not d.get("active") and (time.time() - float(d.get("finished_at", 0))) <= within_sec:
        return d
    return {}


def _used_file(key: str) -> str:
    return os.path.join(DATA_DIR, "_used_" + _safe(key) + ".json")


def used_map(key: str) -> dict:
    """这个群每道题最后一次出题时间 {题号: 时间戳}。只记题号和时间，不碰聊天内容。"""
    try:
        with open(_used_file(key), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return {str(k): int(v or 0) for k, v in d.items()}
    except Exception:
        pass
    return {}


def mark_used(key: str, puzzle_id: str, ts: int | None = None) -> None:
    pid = str(puzzle_id or "")
    if not pid:
        return
    d = used_map(key)
    d[pid] = int(ts if ts is not None else time.time())
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        p = _used_file(key)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, p)
    except Exception:
        pass


def pick_puzzle(key: str, puzzles: list, cooldown_sec: int = 365 * 24 * 3600,
                avoid: list | None = None, category: str = "") -> tuple:
    """按「这个群一年内不重出」挑一道题；category 非空时只在这个类别里挑。

    返回 (题目, 是否因为题库轮完而破例重出)。
    都不满足冷却时，退而求其次挑「最久以前出过」的那道，并告诉调用方一句实话。
    """
    import random as _random
    used = used_map(key)
    avoid = [str(x) for x in (avoid or []) if x]
    now = int(time.time())
    if category:
        pool = [x for x in puzzles if str(x.get("category") or "") == str(category)]
        if not pool:
            # 这个类别一道都没有（AI 还没编过）→ 让调用方去现编，这里返回空池时兜底
            pool = []
    else:
        pool = list(puzzles)
    pool = [x for x in pool if str(x.get("id")) not in avoid] or pool
    if not pool:
        return None, True
    fresh = [x for x in pool
             if str(x.get("id")) not in used
             or now - int(used.get(str(x.get("id"))) or 0) >= int(cooldown_sec or 0)]
    if fresh:
        return _random.choice(fresh), False
    oldest = min(pool, key=lambda x: used.get(str(x.get("id")), 0))
    return oldest, True


def fresh_count(key: str, puzzles: list, cooldown_sec: int = 365 * 24 * 3600,
                category: str = "") -> int:
    """这个群里还有几道题是「没出过 / 冷却期已过」的（用来判断该不该提前补货）。"""
    used = used_map(key)
    now = int(time.time())
    if category:
        puzzles = [x for x in puzzles if str(x.get("category") or "") == str(category)]
    return len([x for x in puzzles
                if str(x.get("id")) not in used
                or now - int(used.get(str(x.get("id"))) or 0) >= int(cooldown_sec or 0)])


def end(key: str, no_disk: bool = False) -> None:
    d = load(key, no_disk)
    if d:
        d["active"] = False
        save(key, d, no_disk)
    if no_disk:
        _MEM.pop(key, None)
        return
    try:
        os.remove(path(key))
    except Exception:
        pass


def fmt_asked(d: dict) -> str:
    """这局玩家问过的问题（给"讨提示"用，避免提示重复他已经问过的方向）。"""
    rows = d.get("qa") or []
    qs = [str(r.get("q") or "").strip() for r in rows if r.get("q")]
    return " ｜ ".join(qs)


def fmt_log(d: dict, limit: int = 150) -> str:
    """这局问过的**全部**问答（给"看进度"总结用）。"""
    rows = (d.get("log") or d.get("qa") or [])[-limit:]
    return "\n".join("%s → %s" % (str(r.get("q") or "")[:60], str(r.get("a") or "").replace("\n", " / ")[:40])
                     for r in rows)


def add_qa(key: str, d: dict, who: str, q: str, a: str, keep: int = 12,
           no_disk: bool = False) -> dict:
    if "log" not in d:
        d["log"] = list(d.get("qa") or [])          # 老会话：先把已有的搬过来
    d["log"].append({"who": who, "q": q, "a": a})
    d["log"] = d["log"][-150:]                      # 只给"看进度"用，够长就行
    d.setdefault("qa", []).append({"who": who, "q": q, "a": a})
    d["qa"] = d["qa"][-keep:]
    d["asked"] = int(d.get("asked", 0)) + 1
    if who and who not in (d.get("players") or []):
        d.setdefault("players", []).append(who)
    save(key, d, no_disk)
    return d


def similar_qa(d: dict, text: str, threshold: float = 0.72) -> tuple:
    """在这局的问答历史里找"跟这句最像的那个问题"（用来保证改个说法问，答案不变）。"""
    import difflib
    def _n(x):
        return re.sub(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()【】\[\]…—\-～~？?]", "", str(x or ""))
    _STOP = set("的了是在有和与就都也不一他她它们这那什么为因所以但而且还只很太又再你我上下里外中候会能要想让把被从到对跟个之其如果没吗呢吧啊么")
    def _grams(x):
        out = set()
        for i in range(len(x) - 1):
            g = x[i:i + 2]
            if any(ch in _STOP for ch in g):
                continue
            out.add(g)
        return out
    t = _n(text)
    if len(t) < 4:
        return ("", "")
    tg = _grams(t)
    best, best_r, best_s = ("", ""), 0.0, 0
    for r in d.get("qa") or []:
        q = _n(r.get("q") or "")
        if not q:
            continue
        ratio = difflib.SequenceMatcher(None, t, q).ratio()
        shared = len(tg & _grams(q))
        score = ratio + 0.08 * shared          # 字面像 或 实词重合多，都算"同一个意思"
        if score > best_r:
            best, best_r, best_s = (str(r.get("q") or ""), str(r.get("a") or "")), score, shared
    # 门槛：字面 ≥threshold，或者至少 2 个实词重合（换个说法问同一件事）
    if best_r >= threshold or best_s >= 2:
        return best
    return ("", "")


def fmt_history(d: dict) -> str:
    rows = d.get("qa") or []
    if not rows:
        return ""
    return "\n".join("问：%s\n答：%s" % (r.get("q", ""), r.get("a", "")) for r in rows)


def fmt_facts(d: dict) -> str:
    fs = d.get("facts") or []
    return "\n".join("- %s" % x for x in fs) if fs else ""
