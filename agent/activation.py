# -*- coding: utf-8 -*-
"""群激活状态机：新群默认未激活 → 主人 @ 一下才激活 → 主人说「关机」退回未激活。

规则（按用户要求）：
- 任何新群（没被激活过的群）默认**未激活**：她不说话、不记上下文、不收图、不参与任何流程
- 只有**主人**（owner_ids 里的人）**@她**，才把这个群激活（激活那条消息照常回答，等于"开机"）
- 已激活的群里，主人说「关机 / 关闭 / 下线 / 退下…」（这条可以不带 @，但必须是**短命令**，
  不能是"这台机器怎么关机"这种句子）→ 退回未激活，并回一句短的确认
- 私聊不受影响（私聊不激活／不关闭）
"""
import re
import time

# 「关机」类命令：整句就是命令才算（短、且没有别的内容）
_POWEROFF_WORDS = ("关机", "关闭", "关一下", "关掉", "下机", "下线", "退下", "先退下", "退了",
                   "别说了", "安静", "闭嘴", "散了吧", "休息吧", "休眠")
_POWEROFF_RE = re.compile(
    r"^[\s，。、！!~～.?？…]*(" + "|".join(_POWEROFF_WORDS) + r")[\s，。、！!~～.?？…]*$")
# 允许显式"开机"（有 @ 就行，这个只是让"@她 开机"也能被识别为激活意图）
_ON_WORDS = ("开机", "上线", "上班", "开工", "醒来", "来吧")
_ON_RE = re.compile(r"^[\s，。、！!~～.?？…]*(" + "|".join(_ON_WORDS) + r")[\s，。、！!~～.?？…]*$")
_AT_PREFIX_RE = re.compile(r"^(?:\s*@[^\s@]{0,32}(?:\([0-9]{4,12}\))?\s*)+")


def strip_at(text: str) -> str:
    """去掉开头的 @某人（含 @昵称(QQ) 写法），只留正文。"""
    t = str(text or "")
    prev = None
    while prev != t:
        prev = t
        t = _AT_PREFIX_RE.sub("", t)
    return t.strip()


def is_poweroff(text: str, max_len: int = 12) -> bool:
    """这句是不是"关机"命令（短命令才算，避免"怎么关机"这种疑问被误判）。"""
    body = strip_at(text)
    if not body or len(body) > max_len:
        return False
    return bool(_POWEROFF_RE.match(body))


def is_on_command(text: str, max_len: int = 12) -> bool:
    body = strip_at(text)
    return bool(body) and len(body) <= max_len and bool(_ON_RE.match(body))


def merge_state(old: dict, gid, on: bool, by: str = "") -> dict:
    """写入/更新某个群的激活状态（纯函数，方便离线测）。"""
    d = dict(old or {})
    g = d.get("groups")
    if not isinstance(g, dict):
        g = {}
    g[str(gid)] = {"on": bool(on), "by": str(by or ""), "ts": int(time.time())}
    d["groups"] = g
    return d


def is_active(state: dict, gid) -> bool:
    try:
        return bool(((state or {}).get("groups") or {}).get(str(gid), {}).get("on"))
    except Exception:
        return False


def seed_from(groups) -> dict:
    """把一批群号先标成"已激活"（上线这个功能时用，免得现有群突然全哑了）。"""
    d = {"groups": {}}
    for g in (groups or []):
        g = str(g or "").strip()
        if g and g not in ("0",):
            d["groups"][g] = {"on": True, "by": "seed", "ts": int(time.time())}
    return d
