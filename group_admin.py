#!/usr/bin/env python3
"""大肥鱼 群管理小工具（改 config.json，热加载立即生效）

  python3 group_admin.py                 # 列出所有群状态
  python3 group_admin.py allow <群号>    # 允许她在该群说话（并建卡）
  python3 group_admin.py block <群号>    # 禁止她在该群说话
  python3 group_admin.py new <群号> [名字]  # 手动建人格卡
"""
import json, os, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "config.json")
REG = os.path.join(HERE, "data", "group_registry.json")


def load():
    return json.load(open(CFG, encoding="utf-8"))


def save(c):
    tmp = CFG + ".tmp"
    json.dump(c, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, CFG)


def reg_load():
    try:
        return json.load(open(REG, encoding="utf-8"))
    except Exception:
        return {}


def reg_save(d):
    os.makedirs(os.path.dirname(REG), exist_ok=True)
    json.dump(d, open(REG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def ensure_card(c, gid, name=""):
    pdir = os.path.join(HERE, "personas")
    os.makedirs(pdir, exist_ok=True)
    card = os.path.join(pdir, "%s.txt" % gid)
    if not os.path.exists(card):
        tpl = os.path.join(pdir, "_template.txt")
        src = tpl if os.path.exists(tpl) else os.path.join(HERE, "system_prompt.txt")
        body = open(src, encoding="utf-8").read().strip()
        open(card, "w", encoding="utf-8").write(
            "[本群：%s｜建卡于 %s]\n\n%s\n" % (name or ("群" + gid[-4:]), time.strftime("%Y-%m-%d"), body))
    pb = c.setdefault("prompt_by_group", {})
    pb[gid] = "personas/%s.txt" % gid
    r = reg_load()
    r[gid] = {"name": name, "persona": "personas/%s.txt" % gid, "created": int(time.time())}
    reg_save(r)
    return card


def main(argv):
    c = load()
    allowed = [str(x) for x in (c.get("allowed_groups") or [])]
    pb = c.get("prompt_by_group") or {}
    mem = [str(x) for x in ((c.get("memory") or {}).get("groups") or [])]
    if not argv:
        reg = reg_load()
        print("群号          说话  人格卡                        记忆   群名")
        for gid in sorted(set(list(pb.keys()) + allowed + list(reg.keys()))):
            if gid == "private":
                continue
            print("%-12s  %-4s  %-28s  %-5s  %s" % (
                gid, "✅" if gid in allowed else "❌", pb.get(gid, "(默认温和版)"),
                "✅" if gid in mem else "❌", (reg.get(gid) or {}).get("name", "")))
        return 0
    cmd = argv[0]
    if cmd in ("allow", "block", "new") and len(argv) > 1:
        gid = str(argv[1])
        name = argv[2] if len(argv) > 2 else ""
        if cmd == "new":
            ensure_card(c, gid, name)
            save(c)
            print("已建卡：personas/%s.txt（未放行，用 allow 放行）" % gid)
            return 0
        if cmd == "allow":
            ensure_card(c, gid, name)
            if gid not in allowed:
                allowed.append(gid)
            if gid not in mem:
                mem.append(gid)
            print("已放行 %s（并加入记忆）" % gid)
        else:
            allowed = [x for x in allowed if x != gid]
            print("已静音 %s" % gid)
        c["allowed_groups"] = allowed
        c.setdefault("memory", {})["groups"] = mem
        save(c)
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
