#!/usr/bin/env python3
"""生成/更新「人物档案」。由 refresh.py 调用，也可单独跑：python3 build_members.py <群号>"""
import collections
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memory import llm, prompts, store  # noqa: E402


def build(gid: str, cfg: dict, *, window: int = 300, limit: int = 120,
          min_msgs_create: int = 5, min_new: int = 20, force: bool = False) -> dict:
    rows = store.load_chatlog(gid, limit=window)
    if len(rows) < 30:
        store.log("群 %s 素材不足（%d 条），跳过人物档案" % (gid, len(rows)))
        return {}
    old = store.read_members(gid)
    old_members = old.get("members") or {}

    # 按人聚合
    by_uid = collections.OrderedDict()
    for d in rows:
        uid = str(d.get("u") or "")
        if not uid:
            continue
        e = by_uid.setdefault(uid, {"name": d.get("n") or uid, "msgs": []})
        e["name"] = d.get("n") or e["name"]
        e["msgs"].append(" ".join((d.get("x") or "").split())[:60])

    todo = {}
    for uid, e in by_uid.items():
        n = len(e["msgs"])
        if n < min_msgs_create:
            continue                      # 没怎么发过言的 → 不认识
        prev = old_members.get(uid) or {}
        if prev.get("profile") and n - int(prev.get("msg_count", 0)) < min_new and not force:
            continue                      # 增量不够，沿用旧档案（--force 时忽略）
        todo[uid] = e
    if not todo:
        store.log("群 %s 没有需要更新档案的人" % gid)
        return {}

    material = "\n\n".join("【%s】\n%s" % (e["name"], "\n".join(e["msgs"][-40:]))
                           for uid, e in todo.items())
    try:
        text, usage = llm.chat(
            [{"role": "user", "content": prompts.members_prompt(material, limit=limit)}],
            cfg, json_mode=bool(((cfg.get("memory") or {}).get("provider") or {}).get("json_mode", False)),
            max_tokens=3000,
        )
        data = store.parse_json(text)
    except Exception as e:
        store.log("群 %s 人物档案生成失败：%r（保留旧版）" % (gid, e))
        return {}

    # 校验：evidence 必须出自该人自己的发言
    name2uid = {e["name"]: uid for uid, e in by_uid.items()}
    added = 0
    for m in (data.get("members") or []):
        nm = str(m.get("name") or "").strip()
        prof = str(m.get("profile") or "").strip()
        uid = name2uid.get(nm)
        if not uid or not prof:
            continue
        own = "\n".join(by_uid[uid]["msgs"])
        if not store.evidence_ok(m.get("evidence") or [], own):
            store.log("档案证据校验不过，丢弃：%s" % nm)
            continue
        old_members[uid] = {
            "name": nm, "profile": store.trim_smart(prof, limit), "msg_count": len(by_uid[uid]["msgs"]),
            "evidence": (m.get("evidence") or [])[:3], "updated_at": int(time.time()),
        }
        added += 1
    if added:
        store.write_members(gid, {"members": old_members})
        _c = llm.estimate_cost(usage, cfg)
        store.log("群 %s 人物档案已更新 %d 人（候选 %d，总 %d）｜%s%s"
                  % (gid, added, len(todo), len(old_members), llm.usage_note(usage),
                     "" if _c == 0 else "｜约 ¥%.4f" % _c))
    else:
        store.log("群 %s 没有通过校验的档案，未改动" % gid)
    return old_members


if __name__ == "__main__":
    g = sys.argv[1] if len(sys.argv) > 1 else ""
    c = llm.load_config()
    m = (c.get("memory") or {}).get("members") or {}
    r = build(g, c, window=int(m.get("window", 300)), limit=int(m.get("max_chars", 120)),
              min_msgs_create=int(m.get("min_msgs_to_create", 5)),
              min_new=int(m.get("min_new_msgs_to_update", 20)),
              force=("--force" in sys.argv))
    for uid, v in r.items():
        print("  %-14s %s" % (v.get("name"), v.get("profile")))
