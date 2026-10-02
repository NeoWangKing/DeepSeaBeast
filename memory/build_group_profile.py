#!/usr/bin/env python3
"""生成/更新「群印象」。由 refresh.py 调用，也可单独跑：python3 build_group_profile.py <群号>"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memory import llm, prompts, store  # noqa: E402


def build(gid: str, cfg: dict, *, window: int = 200, limit: int = 200, force: bool = False) -> bool:
    rows = store.load_chatlog(gid, limit=window)
    if len(rows) < 20:
        store.log("群 %s 素材不足（%d 条），跳过群印象" % (gid, len(rows)))
        return False
    material = store.fmt_lines(rows)
    try:
        text, usage = llm.chat(
            [{"role": "user", "content": prompts.group_prompt(material, limit=limit)}],
            cfg, json_mode=bool(((cfg.get("memory") or {}).get("provider") or {}).get("json_mode", False)),
            max_tokens=1500,
        )
        data = store.parse_json(text)
    except Exception as e:
        store.log("群 %s 群印象生成失败：%r（保留旧版）" % (gid, e))
        return False

    body = str(data.get("profile") or "").strip()
    ev = data.get("evidence") or []
    if not body:
        store.log("群 %s 返回空内容，保留旧版" % gid)
        return False
    if not store.evidence_ok(ev, material):
        store.log("群 %s 证据校验不过（可能是编的），保留旧版｜evidence=%s" % (gid, str(ev)[:80]))
        return False
    body = store.trim_smart(body, limit)
    base = ((cfg.get("memory") or {}).get("provider") or {}).get("model", "?")
    meta = "%s｜素材 %d 条群消息（%s ~ %s）｜更新于 %s｜%s" % (
        base, len(rows),
        time.strftime("%m-%d %H:%M", time.localtime(rows[0]["t"])),
        time.strftime("%m-%d %H:%M", time.localtime(rows[-1]["t"])),
        time.strftime("%Y-%m-%d %H:%M"), llm.usage_note(usage))
    store.write_profile(gid, body, meta)
    _c = llm.estimate_cost(usage, cfg)
    store.log("群 %s 群印象已更新（%d 字，证据 %d 条）｜%s%s"
              % (gid, len(body), len(ev), llm.usage_note(usage),
                 "" if _c == 0 else "｜约 ¥%.4f" % _c))
    return True


if __name__ == "__main__":
    g = sys.argv[1] if len(sys.argv) > 1 else ""
    c = llm.load_config()
    m = (c.get("memory") or {}).get("group_profile") or {}
    ok = build(g, c, window=int(m.get("window", 200)), limit=int(m.get("max_chars", 200)))
    print(store.read_profile(g) if ok else "(未更新)")
