# -*- coding: utf-8 -*-
"""技能（skill）层：常驻提示词里只放"名字 + 一句话"，要用的时候才把全文拉进上下文。

文件格式跟 AstrBot 自带 Skills / Claude·Codex 的 SKILL.md 一致（frontmatter + 正文）：

    skills/<name>/SKILL.md
    ---
    name: turtle_soup
    description: 海龟汤怎么开局、怎么判、怎么收尾
    triggers: 海龟汤, 猜谜, 推理游戏
    ---
    （正文：给模型的完整说明书，可以写很长）

为什么不全塞常驻提示词：常驻段落每轮都要花钱、还稀释注意力；技能是"按需展开"。
为什么不用 AstrBot 自带的 Skills：那套是给它自己的 agent runner 用的，我们跑的是自己的工具循环，
所以「格式兼容、机制自己实现」——一个 use_skill 工具 + 一份索引就够了。
"""
import os
import re

_CACHE = {"t": 0.0, "root": "", "items": []}
_CACHE_TTL = 30
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")


def skills_root(plugin_dir: str = "") -> str:
    base = plugin_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "skills")


def parse_frontmatter(text: str) -> tuple:
    """返回 (meta, body)。没 frontmatter 就只当正文。"""
    s = str(text or "")
    m = _FM_RE.match(s)
    if not m:
        return {}, s.strip()
    meta = {}
    for line in (m.group(1) or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip().strip('"').strip("'")
        meta[k.strip().lower()] = v
    return meta, s[m.end():].strip()


def discover(plugin_dir: str = "", ttl: int = _CACHE_TTL) -> list:
    """扫 skills/*/SKILL.md（也兼容 skills/<name>.md）。返回 [{name, description, triggers, path}]。"""
    root = skills_root(plugin_dir)
    import time as _t
    if (_CACHE["root"] == root and _t.time() - float(_CACHE["t"] or 0) < max(1, int(ttl or 1))):
        return list(_CACHE["items"])
    items = []
    try:
        names = sorted(os.listdir(root))
    except Exception:
        names = []
    for n in names:
        if n.startswith(".") or n == "README.md":
            continue
        p = os.path.join(root, n)
        cand = os.path.join(p, "SKILL.md") if os.path.isdir(p) else (p if n.endswith(".md") else "")
        if not cand or not os.path.isfile(cand):
            continue
        try:
            with open(cand, encoding="utf-8") as f:
                meta, body = parse_frontmatter(f.read())
        except Exception:
            continue
        name = str(meta.get("name") or "").strip()      # 没有 frontmatter/name 的不算技能
        if not name or not _NAME_RE.match(name):
            continue
        items.append({"name": name,
                      "description": str(meta.get("description") or "").strip(),
                      "triggers": str(meta.get("triggers") or "").strip(),
                      "path": cand,
                      "chars": len(body)})
    items.sort(key=lambda x: x["name"])
    _CACHE.update({"t": _t.time(), "root": root, "items": items})
    return list(items)


def names(plugin_dir: str = "") -> list:
    return [x["name"] for x in discover(plugin_dir)]


def load(plugin_dir: str, name: str) -> str:
    """取某个技能的正文（全文）。找不到返回 ""。"""
    want = str(name or "").strip()
    if not want:
        return ""
    for it in discover(plugin_dir):
        if it["name"] == want:
            try:
                with open(it["path"], encoding="utf-8") as f:
                    _meta, body = parse_frontmatter(f.read())
                return body
            except Exception:
                return ""
    return ""


def index_text(plugin_dir: str = "", max_desc: int = 70) -> str:
    """常驻提示词里的技能索引（只有名字 + 一句话 + 触发词）。没有技能就返回空串。"""
    lines = []
    for it in discover(plugin_dir):
        desc = re.sub(r"\s+", " ", it.get("description") or "").strip()[:max_desc]
        trig = re.sub(r"\s+", " ", it.get("triggers") or "").strip()[:60]
        line = "- %s：%s" % (it["name"], desc or "（没有说明）")
        if trig:
            line += "（触发：%s）" % trig
        lines.append(line)
    return "\n".join(lines)
