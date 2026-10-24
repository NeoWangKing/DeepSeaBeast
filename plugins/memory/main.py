#!/usr/bin/env python3
"""记忆插件：管理"全局记忆空间 + 每个会话的独立记忆空间"。

一条原则：**能不能记住、记什么、改什么，由模型决定**；本插件只提供存储与注入。

空间：
  global          全局（她自己的经验、关于主人的稳定事实）
  <scope_key>     每个会话一份（群 <群key> / 私聊 / 终端）

流程：
  1) 订阅 message.received → 原始素材进 buffer（滚动保留）
  2) 后台 job（默认 15 分钟）→ 把 buffer 交给模型 → 模型输出 add/update/delete 操作
  3) 应用操作 → 落盘
  4) get_context → 按"相关性 + 重要度 + 新鲜度"挑几条注入提示词

存储只用 JSON 文件，不引入任何依赖。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import LLM  # noqa: E402

DATA = os.environ.get("DEEPSEA_PLUGIN_DATA") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data")
SPACES = os.path.join(DATA, "spaces")
BUFFER = os.path.join(DATA, "buffer")
STATE = os.path.join(DATA, "state.json")

BUFFER_MAX = 80          # 每个会话最多留多少条原始素材
BUFFER_TRIGGER = 6       # 攒够这么多条就可以让模型看一眼
STALE_SEC = 1800         # 或者最老的一条超过 30 分钟
MAX_ENTRIES = 60         # 一个空间最多多少条记忆，超了要合并
GLOBAL_TOP = 6           # 注入时全局空间取几条
SCOPE_TOP = 8            # 注入时会话空间取几条
ENTRY_MAX_CHARS = 120

llm = LLM({})


def log(msg: str) -> None:
    sys.stderr.write("[memory] %s\n" % msg)
    sys.stderr.flush()


# ── 存储 ──────────────────────────────────────────────
def _ensure_dirs() -> None:
    os.makedirs(SPACES, exist_ok=True)
    os.makedirs(BUFFER, exist_ok=True)


def _space_path(name: str) -> str:
    return os.path.join(SPACES, "%s.json" % _safe(name))


def _buffer_path(scope: str) -> str:
    return os.path.join(BUFFER, "%s.jsonl" % _safe(scope))


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name or "unknown"))[:80]


def load_space(name: str) -> dict:
    try:
        with open(_space_path(name), encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    return {"name": name, "entries": list(d.get("entries") or []),
            "updated": d.get("updated") or 0}


def save_space(name: str, data: dict) -> None:
    _ensure_dirs()
    data["updated"] = time.time()
    p = _space_path(name)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0)
    os.replace(tmp, p)


def next_id(data: dict) -> str:
    n = len(data.get("entries") or []) + 1
    used = {e.get("id") for e in data.get("entries") or []}
    while ("m-%04d" % n) in used:
        n += 1
    return "m-%04d" % n


# ── 素材缓冲 ──────────────────────────────────────────
def append_buffer(scope: str, who: str, text: str, ts: float) -> None:
    _ensure_dirs()
    p = _buffer_path(scope)
    rows: list = []
    try:
        with open(p, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
    except Exception:
        rows = []
    text = str(text or "").strip()
    if not text:
        return
    rows.append({"who": who, "text": text[:300], "ts": ts})
    rows = rows[-BUFFER_MAX:]
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, p)


def read_buffer(scope: str) -> list[dict]:
    try:
        with open(_buffer_path(scope), encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except Exception:
        return []


def clear_buffer(scope: str) -> None:
    try:
        os.unlink(_buffer_path(scope))
    except Exception:
        pass


def all_scopes() -> list[str]:
    try:
        return [f[:-6] for f in os.listdir(BUFFER) if f.endswith(".jsonl")]
    except Exception:
        return []


# ── 给模型看的文本 ────────────────────────────────────
def format_entries(entries: list[dict], limit: int = 40) -> str:
    if not entries:
        return "（空）"
    rows = []
    for e in entries[-limit:]:
        tags = (" #" + "/".join(e.get("tags") or [])) if e.get("tags") else ""
        rows.append("%s [%d]%s %s" % (e.get("id"), int(e.get("importance") or 3), tags,
                                      e.get("text")))
    return "\n".join(rows)


EXTRACT_PROMPT = """你在维护一个长期记忆库。下面是【已有记忆】和【最近对话】。

任务：判断最近对话里有没有**值得长期记住**的东西，并给出要执行的操作。

什么值得记（长期有效、以后还用得上）：
- 关于某个人的稳定事实/偏好（"他讨厌被叫X"、"他在写一个 Rust 项目"）
- 这个群的规矩、氛围、常用梗、常聊话题
- 你自己踩过的坑或总结出的经验（"群里贴长代码会被嫌弃"）

什么不要记（一次性的、过一会儿就没用的）：
- 具体的寒暄、临时安排、正在聊的某件小事
- 已经记过、没有变化的内容
- 你自己的回复内容

空间（很重要）：
- global：**只放你自己的经验和通用原则**（"别在群里贴长代码"这类）。
  **任何关于具体人的事实都不要放 global**——global 是所有地方都能看到的，
  在私聊里知道的事写进 global 就等于泄露到群里。
- session：关于具体人/具体群的一切（谁在做什么、群氛围、成员特点、本群梗）

输出**严格 JSON**，不要解释、不要代码块围栏：
{"ops": [
  {"op": "add", "space": "global|session", "text": "一句话，具体、可检索", "importance": 1-5, "tags": ["偏好"], "public": true},

- **只有 space=global 时才需要 `"public": true`**，而且必须真的是"谁看到都无所谓"的
  （她的通用经验、群规、公共梗、项目背景）。凡涉及某个人、某个群的具体事情，
  space 一律写 session，不要写 global。
- 不写 `public` 却又指定 global 的操作会被程序**直接丢弃**（不是提示，是硬拒绝）。
  {"op": "update", "space": "...", "id": "m-0003", "text": "改写成完整的句子", "importance": 4},
  {"op": "delete", "space": "...", "id": "m-0005", "reason": "过时了"}
]}
没有值得记的就输出 {"ops": []}。一次最多 5 个操作，宁少勿多。
"""


def parse_json_reply(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


# ── 应用模型给的操作 ──────────────────────────────────
def apply_ops(scope: str, ops: list[dict]) -> dict:
    done = {"add": 0, "update": 0, "delete": 0, "skipped": 0}
    spaces: dict[str, dict] = {}

    def space(name: str) -> dict:
        key = "global" if name == "global" else scope
        if key not in spaces:
            spaces[key] = load_space(key)
        return spaces[key]

    for op in ops[:8]:
        if not isinstance(op, dict):
            done["skipped"] += 1
            continue
        kind = str(op.get("op") or "").lower()
        target = str(op.get("space") or "session")
        data = space(target)
        text = str(op.get("text") or "").strip()[:ENTRY_MAX_CHARS]
        imp = max(1, min(5, int(op.get("importance") or 3)))

        # global 空间只收"公开"条目：任何关于具体人/群的事都不许放进去
        public = bool(op.get("public")) or target != "global"
        if target == "global" and not public:
            done["rejected_global"] = done.get("rejected_global", 0) + 1
            continue

        if kind == "add" and text:
            # 简单的去重：已有相同/高度相似的就不再存
            if any(text == e.get("text") for e in data["entries"]):
                done["skipped"] += 1
                continue
            data["entries"].append({
                "id": next_id(data), "text": text, "importance": imp,
                "tags": [str(t)[:12] for t in (op.get("tags") or [])][:5],
                "created": time.time(), "updated": time.time(), "uses": 0,
                "source": scope, "public": public,
            })
            done["add"] += 1
        elif kind == "update":
            if target == "global" and not public:
                done["rejected_global"] = done.get("rejected_global", 0) + 1
                continue
            for e in data["entries"]:
                if e.get("id") == op.get("id"):
                    if text:
                        e["text"] = text
                    e["importance"] = imp
                    e["updated"] = time.time()
                    done["update"] += 1
                    break
            else:
                done["skipped"] += 1
        elif kind == "delete":
            before = len(data["entries"])
            data["entries"] = [e for e in data["entries"] if e.get("id") != op.get("id")]
            done["delete" if len(data["entries"]) < before else "skipped"] += 1
        else:
            done["skipped"] += 1

    for name, data in spaces.items():
        save_space(name, data)
    return done


# ── 相关性排序（没有向量，用字面 + 重要度 + 新鲜度）──
def tokens(text: str) -> set[str]:
    t = str(text or "").lower()
    out = set(re.findall(r"[a-z0-9]{2,}", t))
    cjk = re.findall(r"[\u4e00-\u9fff]", t)
    out |= {"".join(cjk[i:i + 2]) for i in range(len(cjk) - 1)}
    return out


def rank(entries: list[dict], query: str) -> list[dict]:
    q = tokens(query)
    now = time.time()
    scored = []
    for e in entries:
        hit = len(q & tokens(e.get("text")))
        age_days = max(0.0, (now - float(e.get("updated") or 0)) / 86400)
        score = hit * 3 + float(e.get("importance") or 3) - min(age_days, 30) * 0.1
        scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    return [e for _s, e in scored]


# ── 后台任务 ──────────────────────────────────────────
def run_consolidate() -> dict:
    if not llm.ready:
        return {"ok": False, "error": "模型未配置"}
    touched, total_ops = 0, 0
    for scope in all_scopes():
        rows = read_buffer(scope)
        if not rows:
            continue
        oldest = float(rows[0].get("ts") or 0)
        if len(rows) < BUFFER_TRIGGER and (time.time() - oldest) < STALE_SEC:
            continue

        session = load_space(scope)
        lines = "\n".join("%s：%s" % (r.get("who") or "?", r.get("text")) for r in rows[-40:])
        msgs = [
            {"role": "system", "content": EXTRACT_PROMPT},
            {"role": "user", "content":
                "【已有记忆 · 全局】\n%s\n\n【已有记忆 · 本会话】\n%s\n\n【最近对话】\n%s"
                % (format_entries(load_space("global")["entries"]),
                   format_entries(session["entries"]), lines)},
        ]
        try:
            reply = llm.chat(msgs, max_tokens=700, temperature=0.2)
        except Exception as e:
            log("整理 %s 失败：%r" % (scope, e))
            continue
        ops = (parse_json_reply(reply) or {}).get("ops") or []
        done = apply_ops(scope, ops)
        clear_buffer(scope)
        touched += 1
        total_ops += done["add"] + done["update"] + done["delete"]
        log("整理 %s：%s（%d 条素材）" % (scope, done, len(rows)))
    if touched:
        save_state({"last_run": time.time()})
    return {"ok": True, "data": {"scopes": touched, "ops": total_ops}}


def run_prune() -> dict:
    """空间太大时让模型合并/丢弃（低频）。"""
    if not llm.ready:
        return {"ok": False, "error": "模型未配置"}
    pruned = 0
    for name in ["global"] + all_scopes():
        data = load_space(name)
        if len(data["entries"]) <= MAX_ENTRIES:
            continue
        msgs = [
            {"role": "system", "content":
                "下面是一个记忆库的全部条目。请合并重复、删掉过时或琐碎的，保留最重要的 ≤%d 条。"
                "输出严格 JSON：{\"keep\": [\"m-0001\", ...]}" % (MAX_ENTRIES - 10)},
            {"role": "user", "content": format_entries(data["entries"], limit=200)},
        ]
        try:
            reply = llm.chat(msgs, max_tokens=600, temperature=0.2)
        except Exception as e:
            log("压缩 %s 失败：%r" % (name, e))
            continue
        keep = set(parse_json_reply(reply).get("keep") or [])
        if not keep:
            continue
        before = len(data["entries"])
        data["entries"] = [e for e in data["entries"] if e.get("id") in keep]
        save_space(name, data)
        pruned += before - len(data["entries"])
    return {"ok": True, "data": {"pruned": pruned}}


def save_state(d: dict) -> None:
    _ensure_dirs()
    try:
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, STATE)
    except Exception:
        pass


# ── 协议方法 ──────────────────────────────────────────
def get_context(params: dict) -> dict:
    scope = str((params.get("scope") or {}).get("ref") or (params.get("scope") or {}).get("key") or "unknown")
    query = str(params.get("message") or "")

    gl_all = [e for e in load_space("global")["entries"] if e.get("public", True)]
    gl = rank(gl_all, query)[:GLOBAL_TOP]
    sc = rank(load_space(scope)["entries"], query)[:SCOPE_TOP]

    knowledge: list[str] = []
    if gl:
        knowledge.append("【长期记忆】\n" + "\n".join("- " + e["text"] for e in gl))
    if sc:
        knowledge.append("【关于这里的记忆】\n" + "\n".join("- " + e["text"] for e in sc))

    # 记一次"被用到"，供以后淘汰久不使用的条目
    if sc:
        data = load_space(scope)
        used = {e.get("id") for e in sc}
        for e in data["entries"]:
            if e.get("id") in used:
                e["uses"] = int(e.get("uses") or 0) + 1
        save_space(scope, data)

    return {"persona": "", "rules": [], "knowledge": knowledge}


def on_event(event: str, data: dict) -> None:
    if event != "message.received":
        return
    scope = str((data.get("scope") or {}).get("ref") or (data.get("scope") or {}).get("ref") or (data.get("scope") or {}).get("key") or "unknown")
    who = str((data.get("sender") or {}).get("name") or "?")
    append_buffer(scope, who, str(data.get("text") or ""), float(data.get("ts") or time.time()))


def handle(method: str, params: dict):
    if method == "hello":
        global llm
        llm = LLM(params.get("llm") or {})
        log("模型：%s @ %s（%s）" % (llm.model, llm.base_url,
                                   "可用" if llm.ready else "不可用，先只攒素材"))
        return {"name": "memory", "version": "1.0", "protocol": 1,
                "capabilities": ["context", "events", "jobs"],
                "events": ["message.received"],
                "jobs": [
                    {"name": "consolidate", "interval_sec": 300, "timeout": 120,
                     "description": "把最近的对话整理进记忆空间"},
                    {"name": "prune", "interval_sec": 86400, "timeout": 180,
                     "description": "空间太大时合并/删减"},
                ],
                "tools": []}
    if method == "get_context":
        return get_context(params)
    if method == "run_job":
        job = str(params.get("job") or "")
        if job == "consolidate":
            return run_consolidate()
        if job == "prune":
            return run_prune()
        return {"ok": False, "error": "没有这个任务：%s" % job}
    if method == "ping":
        return {"pong": True}
    if method == "shutdown":
        return {"ok": True}
    raise ValueError("不认识的方法：%s" % method)


def main() -> None:
    _ensure_dirs()
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
                log("处理事件失败：%r" % (e,))
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
