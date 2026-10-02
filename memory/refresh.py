#!/usr/bin/env python3
"""记忆刷新总入口（给 systemd timer 调，也可手动跑）。

  python3 refresh.py            # 按守卫条件决定要不要跑
  python3 refresh.py --force    # 忽略守卫，强制刷新
  python3 refresh.py --only members
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memory import build_group_profile, build_members, llm, store  # noqa: E402

STATE = os.path.join(store.MEM_DIR, "state.json")


def load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(s: dict) -> None:
    store.atomic_write(STATE, json.dumps(s, ensure_ascii=False, indent=1))


def main(argv: list) -> int:
    force = "--force" in argv
    only = ""
    if "--only" in argv:
        only = argv[argv.index("--only") + 1] if len(argv) > argv.index("--only") + 1 else ""
    cfg = llm.load_config()
    mem = cfg.get("memory") or {}
    store.ensure_dirs()
    if not mem.get("enabled") and not force:
        store.log("memory 未启用，跳过")
        return 0
    groups = mem.get("groups") or []
    gp_cfg = mem.get("group_profile") or {}
    mb_cfg = mem.get("members") or {}
    state = load_state()
    now = time.time()
    ran = 0

    for gid in groups:
        gid = str(gid)
        st = state.setdefault(gid, {})
        rows = store.load_chatlog(gid, limit=1)
        max_ts = rows[-1].get("t", 0) if rows else 0
        # 关键：以"上次成功生成时的最新消息时间"为基准计数，
        # 而不是每次 timer 醒来就推进基准（否则少于 50 条/小时的群永远轮不到更新）
        gp_new = store.speech_volume(gid, float(st.get("group_last_build_ts", 0)))
        mb_new = store.speech_volume(gid, float(st.get("members_last_build_ts", 0)))

        # ---- 群印象 ----
        if (not only or only == "group") and gp_cfg.get("enabled", True):
            elapsed_h = (now - float(st.get("group_updated_at", 0))) / 3600.0
            enough = gp_new >= int(gp_cfg.get("min_new_msgs", 50))
            if force or (enough and elapsed_h >= float(gp_cfg.get("min_interval_hours", 4))):
                if build_group_profile.build(gid, cfg, window=int(gp_cfg.get("window", 200)),
                                             limit=int(gp_cfg.get("max_chars", 200)), force=force):
                    st["group_updated_at"] = now
                    st["group_last_build_ts"] = max_ts
                    ran += 1
            else:
                store.log("群 %s 群印象暂不更新（自上次生成新增 %d 条 / 距上次 %.1fh）" % (gid, gp_new, elapsed_h))

        # ---- 人物档案 ----
        if (not only or only == "members") and mb_cfg.get("enabled", True):
            elapsed_h = (now - float(st.get("members_updated_at", 0))) / 3600.0
            enough = mb_new >= int(mb_cfg.get("min_new_msgs", 50))
            if force or (enough and elapsed_h >= float(mb_cfg.get("min_interval_hours", 12))):
                if build_members.build(gid, cfg, window=int(mb_cfg.get("window", 400)),
                                       limit=int(mb_cfg.get("max_chars", 120)),
                                       min_msgs_create=int(mb_cfg.get("min_msgs_to_create", 5)),
                                       min_new=int(mb_cfg.get("min_new_msgs_to_update", 20)),
                                       force=force):
                    st["members_updated_at"] = now
                    st["members_last_build_ts"] = max_ts
                    ran += 1
            else:
                store.log("群 %s 人物档案暂不更新（自上次更新新增 %d 条 / 距上次 %.1fh）" % (gid, mb_new, elapsed_h))

    save_state(state)
    store.log("本轮完成，执行了 %d 个任务" % ran)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
