#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大肥鱼 Web 管理面板（只监听 127.0.0.1，通过 SSH 隧道访问）。

设计原则（很重要）：
- **独立进程**：不 import 插件主模块、不碰 AstrBot，面板挂了也不影响她说话。
- **只监听本机**：不新增公网端口（`--host 127.0.0.1`），你 ssh -L 隧道过来看。
- **改动可回滚**：每次"应用"前把要动的文件复制到 /root/qqbot-backups/webadmin/<时间戳>/。
- **只写白名单字段**：config.json 按 key 深合并，不认识的顶层键直接拒绝，绝不整份覆盖。
- **应用 = 写文件 + 可选跑 qqbot-reload**：config.json 本身是热加载（改 mtime 即生效），
  人格卡/表情备注每次都是现读现用，所以不重载也行；按钮默认顺手调一次 reload 更稳妥。

用法：
    python3 tools/webadmin.py [--port 8977] [--host 127.0.0.1]
密码：.secrets/webadmin.pass（首次启动会自动生成并打印）
"""
import argparse
import base64
import hmac
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
CONFIG = os.path.join(PLUGIN_DIR, "config.json")
PERSONAS = os.path.join(PLUGIN_DIR, "prompts", "personas")
NAMES_PATH = os.path.join(PERSONAS, "_names.json")     # 只给面板看的"显示名"
PROMPTS = os.path.join(PLUGIN_DIR, "prompts")
BACKUP_ROOT = "/root/qqbot-backups/webadmin"
SECRET = os.path.join(PLUGIN_DIR, ".secrets", "webadmin.pass")
RELOAD_BIN = "/usr/local/bin/qqbot-reload"
THUMB_DIR = os.path.join(PLUGIN_DIR, "data", "stickers", "_thumbs")
THUMB_MAX = 160

# config.json 里允许面板改动的顶层键（白名单，其它一律拒绝）
ALLOW_TOP = {
    "prompt_by_group", "group_tuning", "stickers", "perception", "search", "kb",
    "only_at_groups", "no_context_groups", "record_at_only_groups", "allowed_groups",
    "intro", "admin", "activation", "agent", "max_auto_per_hour", "min_interval_sec",
    "max_cont_per_hour", "min_cont_gap_sec", "cont_window_sec", "max_engaged_streak",
    "engaged_window_sec", "engaged_interval_sec", "engaged_rest_sec", "burst_suppress_sec",
    "auto_reply_probability", "offpeak", "peak_windows",
}
_LOCK = threading.Lock()


# ---------------- 基础 ----------------
def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _atomic_write(path, text):
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def load_cfg():
    return _read_json(CONFIG, {}) or {}


def backup(paths):
    """改动前备份（返回备份目录）。"""
    ts = time.strftime("%Y%m%d-%H%M%S") + ("-%03d" % (int(time.time() * 1000) % 1000))
    dst = os.path.join(BACKUP_ROOT, ts)
    _n = 0
    while os.path.exists(dst):                 # 同一毫秒也不覆盖
        _n += 1
        dst = os.path.join(BACKUP_ROOT, "%s-%d" % (ts, _n))
    try:
        os.makedirs(dst, exist_ok=True)
        for p in paths:
            if os.path.isfile(p):
                shutil.copy2(p, os.path.join(dst, os.path.basename(p)))
    except Exception as e:
        print("[webadmin] 备份失败 %r" % (e,))
    return dst


def deep_merge(dst, patch):
    """按 key 深合并（只动 patch 里出现的键）。"""
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def _type_ok(old, new):
    """新值必须和旧值同类型（旧值是数字就得是数字；没有旧值则只做基本检查）。"""
    if isinstance(old, bool):
        return isinstance(new, bool)
    if isinstance(old, (int, float)):
        if isinstance(new, bool) or not isinstance(new, (int, float)):
            return False
        return new == new and abs(new) < 1e12          # 非 NaN/Inf
    if isinstance(old, str):
        return isinstance(new, str) and len(new) <= 4000
    if isinstance(old, list):
        return isinstance(new, list) and len(new) <= 2000
    if isinstance(old, dict):
        return isinstance(new, dict)
    return True


def _walk_check(old, new, path, errs):
    if new is None:                    # 允许写成 null = 删掉这一项
        return
    if isinstance(new, dict):
        base = old if isinstance(old, dict) else {}
        for k, v in new.items():
            _walk_check(base.get(k), v, "%s.%s" % (path, k), errs)
        return
    if not _type_ok(old, new):
        errs.append("%s 类型不对：期望 %s，给的是 %s"
                    % (path, type(old).__name__ if old is not None else "字符串/数字",
                       type(new).__name__))


def prune_nulls(d):
    """把 None 当成"删掉这个键"（面板点「默认卡」就是删 prompt_by_group 的那一项）。"""
    if not isinstance(d, dict):
        return d
    for k in list(d.keys()):
        if d[k] is None:
            del d[k]
        elif isinstance(d[k], dict):
            prune_nulls(d[k])
    return d


def _del_paths(dst, patch):
    """按 patch 的路径，把 dst 里对应的项删掉（patch 值为 None 的）。"""
    if not isinstance(patch, dict):
        return
    for k, v in patch.items():
        if v is None:
            if isinstance(dst, dict):
                dst.pop(k, None)
        elif isinstance(v, dict) and isinstance(dst.get(k), dict):
            _del_paths(dst[k], v)
    return dst


def validate_patch(patch, cfg=None):
    """白名单 + 类型校验（拿现有 config 当模板），返回错误列表。"""
    errs = []
    cfg = cfg if isinstance(cfg, dict) else load_cfg()
    for k in (patch or {}):
        if k not in ALLOW_TOP:
            errs.append("不允许改这个配置项：%s" % k)
    gt = (patch or {}).get("group_tuning") or {}
    if isinstance(gt, dict):
        for g, kv in gt.items():
            if not (str(g).isdigit() or g == "*"):
                errs.append("群号不对：%s" % g)
            if not isinstance(kv, dict):
                errs.append("group_tuning.%s 必须是对象" % g)
                continue
            for name, val in kv.items():
                if name in ("reply_prob_mult",):
                    try:
                        if not (0 <= float(val) <= 1):
                            errs.append("group_tuning.%s.reply_prob_mult 要在 0~1" % g)
                    except Exception:
                        errs.append("group_tuning.%s.reply_prob_mult 要是数字" % g)
                elif name.startswith(("max_", "min_", "engaged_", "burst_", "cont_")):
                    try:
                        float(val)
                    except Exception:
                        errs.append("group_tuning.%s.%s 要是数字" % (g, name))
    # 其余顶层项按现有配置的类型校验
    for k, v in (patch or {}).items():
        if k == "group_tuning":
            continue
        _walk_check(cfg.get(k), v, str(k), errs)
    return errs


# ---------------- 人格卡 ----------------
def load_names():
    """人格卡显示名：{文件名: 名称}。"""
    try:
        with open(NAMES_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return {str(k): str(v) for k, v in (d or {}).items() if str(v).strip()}
    except Exception:
        return {}


def save_name(fname, title):
    """写显示名（空 = 删掉，回退用文件名）。"""
    fname = os.path.basename(str(fname or ""))
    if not fname.endswith(".txt"):
        return False, "只给 .txt 卡改名"
    d = load_names()
    t = str(title or "").strip()[:40]
    if t:
        d[fname] = t
    else:
        d.pop(fname, None)
    backup([NAMES_PATH])
    _atomic_write(NAMES_PATH, json.dumps(d, ensure_ascii=False, indent=1))
    return True, t or fname


def _auto_title(fname, text):
    """没设名字时，尽量从卡里挑点有用的当名字（比如 [本群：群0222…]）。"""
    for ln in (text or "").splitlines()[:3]:
        ln = ln.strip()
        if ln.startswith("[") and ln.endswith("]") and len(ln) <= 30:
            return ln.strip("[]")
    return ""


def list_personas():
    out = []
    for d, legacy in ((PERSONAS, False), (PROMPTS, True)):
        try:
            names = sorted(os.listdir(d))
        except Exception:
            continue
        for fn in names:
            if not fn.endswith(".txt"):
                continue
            if legacy and not fn.startswith("system_prompt"):
                continue
            p = os.path.join(d, fn)
            try:
                txt = open(p, encoding="utf-8").read()
                mt = int(os.path.getmtime(p))
            except Exception:
                txt, mt = "", 0
            _names = load_names()
            out.append({"name": fn, "legacy": legacy, "chars": len(txt),
                        "mtime": mt, "path": p, "helper": fn.startswith("_"),
                        "title": _names.get(fn) or _auto_title(fn, txt) or fn,
                        "renamed": bool(_names.get(fn))})
    return out


def read_persona(name):
    """按文件名读（personas 优先，其次 prompts/system_prompt*.txt）。"""
    name = os.path.basename(str(name or ""))
    for p in (os.path.join(PERSONAS, name), os.path.join(PROMPTS, name)):
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return f.read(), p
    return None, ""


def save_persona(name, text):
    """保存人格卡正文（只允许 .txt，且在 personas/ 或 prompts/system_prompt*.txt）。"""
    raw = str(name or "").strip()
    # 先校验**原始**名字：不许路径、不许 ..、不许斜杠（basename 会把 ../evil.txt 变成 evil.txt）
    if not raw or len(raw) > 64 or re.search(r"[/\\]|\.\.", raw) \
            or not re.match(r"^[A-Za-z0-9_\-]+\.txt$", raw):
        return False, "文件名不合法（只能字母数字-_，.txt 结尾，不能带路径）"
    name = raw
    cur, p = read_persona(name)
    if p:
        backup([p])
        _atomic_write(p, str(text or ""))
        return True, p
    if name.startswith("system_prompt"):
        p = os.path.join(PROMPTS, name)
    else:
        p = os.path.join(PERSONAS, name)
        os.makedirs(PERSONAS, exist_ok=True)
    _atomic_write(p, str(text or ""))
    return True, p


# ---------------- 表情包（复用插件自己的模块） ----------------
sys.path.insert(0, PLUGIN_DIR)


def _stickers_mod():
    try:
        import stickers                # noqa
        return stickers
    except Exception:
        return None


_CTYPE = {".gif": "image/gif", ".png": "image/png", ".jpg": "image/jpeg",
          ".jpeg": "image/jpeg", ".webp": "image/webp", ".bmp": "image/bmp"}


def sticker_animated(it) -> bool:
    """是不是动图（多帧 gif/webp）。"""
    try:
        S = _stickers_mod()
        p = S.abs_path(it) or ""
        ext = os.path.splitext(p)[1].lower()
        if ext not in (".gif", ".webp"):
            return False
        from PIL import Image
        im = Image.open(p)
        return int(getattr(im, "n_frames", 1) or 1) > 1
    except Exception:
        return False


def sticker_raw(sid):
    """原图路径 + content-type（动图直接发原文件，浏览器自己会动）。"""
    S = _stickers_mod()
    if not S:
        return "", ""
    it = S.find(sid)
    if not it:
        return "", ""
    p = S.abs_path(it) or ""
    if not p or not os.path.isfile(p):
        return "", ""
    return p, _CTYPE.get(os.path.splitext(p)[1].lower(), "application/octet-stream")


def sticker_list():
    S = _stickers_mod()
    if not S:
        return []
    out = []
    for it in (S.load() or []):
        _p = S.abs_path(it) or ""
        out.append({"animated": sticker_animated(it),
                    "fsize": (os.path.getsize(_p) if _p and os.path.isfile(_p) else 0),
                    "id": str(it.get("id")), "desc": str(it.get("desc") or ""),
                    "tags": [str(x) for x in (it.get("tags") or [])],
                    "used": int(it.get("used") or 0),
                    "size": int(it.get("size") or 0),
                    "group": str(it.get("group") or ""),
                    "face_pushed": bool(it.get("face_pushed")),
                    "added_at": int(it.get("added_at") or 0)})
    return out


def sticker_thumb(sid):
    """生成/取缩略图，返回本地路径（失败返回原图路径）。"""
    S = _stickers_mod()
    if not S:
        return ""
    it = S.find(sid)
    if not it:
        return ""
    src = S.abs_path(it) or ""
    if not src or not os.path.isfile(src):
        return ""
    os.makedirs(THUMB_DIR, exist_ok=True)
    dst = os.path.join(THUMB_DIR, "%s.jpg" % str(sid).replace("/", "_"))
    try:
        if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
            return dst
        from PIL import Image
        im = Image.open(src)
        try:
            im.seek(0)
        except Exception:
            pass
        im = im.convert("RGB")
        w, h = im.size
        k = min(THUMB_MAX / float(max(1, w)), THUMB_MAX / float(max(1, h)), 1.0)
        if k < 1.0:
            im = im.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
        im.save(dst, format="JPEG", quality=80)
        return dst
    except Exception:
        return src


def sticker_note(sid, desc, tags):
    S = _stickers_mod()
    if not S:
        return False, "表情模块载入失败"
    p = os.path.join(PLUGIN_DIR, "data", "stickers", "index.json")
    backup([p])
    ok = S.set_note(str(sid), str(desc or ""), [str(x) for x in (tags or [])] or None)
    return bool(ok), ("已保存" if ok else "没找到这张")


def sticker_delete(sid):
    S = _stickers_mod()
    if not S:
        return False, "表情模块载入失败"
    p = os.path.join(PLUGIN_DIR, "data", "stickers", "index.json")
    backup([p])
    ok = S.delete(str(sid))
    return bool(ok), ("已从本地库删除（QQ 面板里的还得在手机上删或点『同步』）" if ok else "没找到这张")


# ---------------- 群 / 激活 ----------------
def active_state():
    return (_read_json(os.path.join(PLUGIN_DIR, "data", "agent_active.json"), {}) or {}) \
        .get("groups") or {}


def set_active(gid, on):
    p = os.path.join(PLUGIN_DIR, "data", "agent_active.json")
    backup([p])
    d = _read_json(p, {}) or {}
    g = d.get("groups")
    if not isinstance(g, dict):
        g = {}
    g[str(gid)] = {"on": bool(on), "by": "webadmin", "ts": int(time.time())}
    d["groups"] = g
    _atomic_write(p, json.dumps(d, ensure_ascii=False, indent=1))
    return True


PANEL_URL = "http://127.0.0.1:5099"
PANEL_PASS_FILES = [os.path.join(PLUGIN_DIR, ".secrets", "panel.pass"),
                    "/root/.qqbot-panel-pass"]
KNOWN_GROUPS = os.path.join(PLUGIN_DIR, "data", "known_groups.json")
_GRP_CACHE = {"ts": 0.0}


def _panel_call(action, params=None, timeout=6):
    """调 SnowLuma 的本地接口（只读）。失败返回 None，不影响面板其它功能。"""
    import urllib.request
    pw = ""
    for f in PANEL_PASS_FILES:
        try:
            pw = open(f, encoding="utf-8").read().strip()
            if pw:
                break
        except Exception:
            continue
    if not pw:
        return None
    try:
        body = json.dumps({"username": "admin", "password": pw}).encode()
        req = urllib.request.Request(PANEL_URL + "/api/login", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        tok = json.loads(urllib.request.urlopen(req, timeout=timeout).read()).get("token") or ""
        if not tok:
            return None
        body = json.dumps({"uin": "3237702352", "action": action,
                           "params": params or {}}).encode()
        req = urllib.request.Request(PANEL_URL + "/api/debug/invoke", data=body, method="POST",
                                     headers={"Authorization": "Bearer " + tok,
                                              "Content-Type": "application/json"})
        d = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        return d.get("data") if d.get("status") == "ok" else None
    except Exception:
        return None


def known_groups(refresh_sec: int = 600) -> dict:
    """机器人实际在的群 → {群号: {name, members}}（带缓存，拉不到就用上次的）。"""
    now = time.time()
    cache = _read_json(KNOWN_GROUPS, {}) or {}
    if cache and (now - float(_GRP_CACHE.get("ts") or 0) < refresh_sec):
        return cache
    d = _panel_call("get_group_list", {"no_cache": True})
    _GRP_CACHE["ts"] = now
    if isinstance(d, list) and d:
        out = {}
        for g in d:
            gid = str((g or {}).get("group_id") or "")
            if gid:
                out[gid] = {"name": str((g or {}).get("group_name") or ""),
                            "members": int((g or {}).get("member_count") or 0)}
        try:
            _atomic_write(KNOWN_GROUPS, json.dumps(out, ensure_ascii=False, indent=1))
        except Exception:
            pass
        return out
    return cache or {}


def groups_overview():
    cfg = load_cfg()
    act = active_state()
    reg = _read_json(os.path.join(PLUGIN_DIR, "data", "group_registry.json"), {}) or {}
    gmap = {str(k): str(v) for k, v in (cfg.get("prompt_by_group") or {}).items()}
    for _g, _v in (reg or {}).items():          # 自动建档的群也有自己的卡
        _pf = os.path.basename(str((_v or {}).get("persona") or ""))
        if _pf and str(_g) not in gmap:
            gmap[str(_g)] = _pf
    known = known_groups()
    gids = (set(gmap) | set(act) | {str(x) for x in (cfg.get("allowed_groups") or [])}
            | set(reg) | set(known))
    notes = (cfg.get("admin") or {}).get("group_notes") or {}
    only_at = {str(x) for x in (cfg.get("only_at_groups") or [])}
    nocontext = {str(x) for x in (cfg.get("no_context_groups") or [])}
    rec_at = {str(x) for x in (cfg.get("record_at_only_groups") or [])}
    kbm = {str(k): str(v) for k, v in ((cfg.get("kb") or {}).get("scope_by_group") or {}).items()}
    stk = cfg.get("stickers") or {}
    send_ex = {str(x) for x in (stk.get("send_exclude_groups") or [])}
    col_ex = {str(x) for x in (stk.get("collect_exclude_groups") or [])}
    allow = {str(x) for x in (cfg.get("allowed_groups") or [])}
    out = []
    for g in sorted(gids):
        if not str(g).isdigit():
            continue
        _k = known.get(str(g)) or {}
        out.append({
            "name": str(_k.get("name") or ""),
            "members": int(_k.get("members") or 0),
            "in_bot": bool(_k),
            "in_allowlist": (not allow) or (str(g) in allow),
            "auto_created": str(g) in (reg or {}),
            "suspect_phantom": (str(g) in (reg or {})) and (not allow or str(g) not in allow)
                               and not str(gmap.get(str(g)) or ""),
            "gid": str(g), "note": str(notes.get(str(g)) or ""),
            "persona": os.path.basename(gmap.get(str(g), "")) or "",
            "active": bool((act.get(str(g)) or {}).get("on")),
            "only_at": str(g) in only_at, "no_context": str(g) in nocontext,
            "record_at_only": str(g) in rec_at,
            "kb_scope": kbm.get(str(g), ""),
            "no_sticker_send": str(g) in send_ex, "no_sticker_collect": str(g) in col_ex,
            "tuning": ((cfg.get("group_tuning") or {}).get(str(g)) or {}),
        })
    return out


# ---------------- 应用 ----------------
def apply_changes(body):
    """body: {config:{…}, personas:{name:text}, active:{gid:bool}, reload:bool}"""
    body = body or {}
    logs = []
    todo = []

    patch = body.get("config") or {}
    errs = validate_patch(patch, load_cfg())
    if errs:
        return {"ok": False, "errors": errs}
    cfg = load_cfg()
    if patch:
        _del_paths(cfg, patch)          # 先按 null 删键
        prune_nulls(patch)              # 剩下的 null 不再写入
        deep_merge(cfg, patch)
        prune_nulls(cfg)
        todo.append(CONFIG)
        logs.append("config.json：改了 %s" % "、".join(sorted(patch.keys())))

    personas = body.get("personas") or {}
    persona_files = []
    for name, text in personas.items():
        ok, p = save_persona(name, text)
        if not ok:
            return {"ok": False, "errors": ["人格卡 %s：%s" % (name, p)]}
        persona_files.append(p)
        logs.append("人格卡 %s：已保存（%d 字）" % (name, len(str(text or ""))))
        # 新建的卡可能同时要挂到某个群（prompt_by_group 在 config patch 里）

    if patch:
        # 备份后再写，且保留原文件里的其它键
        backup([CONFIG] + persona_files)
        _atomic_write(CONFIG, json.dumps(cfg, ensure_ascii=False, indent=2))
    elif persona_files:
        backup(persona_files)

    for gid, on in (body.get("active") or {}).items():
        set_active(gid, bool(on))
        logs.append("群 %s：%s" % (gid, "已激活" if on else "已置为未激活"))

    result = {"ok": True, "logs": logs, "backup": os.path.dirname(BACKUP_ROOT) and
              (os.path.join(BACKUP_ROOT, sorted(os.listdir(BACKUP_ROOT))[-1])
               if os.path.isdir(BACKUP_ROOT) and os.listdir(BACKUP_ROOT) else "")}
    if body.get("reload", True):
        try:
            r = subprocess.run([RELOAD_BIN], capture_output=True, text=True, timeout=180)
            out = (r.stdout or "") + (r.stderr or "")
            ok = ("已加载" in out) or ("重载成功" in out)
            result["reload"] = {"ok": bool(ok), "tail": out[-400:]}
            logs.append("重载：%s" % ("成功" if ok else "看日志"))
        except Exception as e:
            result["reload"] = {"ok": False, "tail": repr(e)}
            logs.append("重载失败：%r（改动已写入，config 是热加载的，通常也已经生效）" % (e,))
    return result


def full_state():
    cfg = load_cfg()
    return {
        "config": {
            "prompt_by_group": cfg.get("prompt_by_group") or {},
            "group_tuning": cfg.get("group_tuning") or {},
            "stickers": cfg.get("stickers") or {},
            "perception": cfg.get("perception") or {},
            "search": cfg.get("search") or {},
            "kb": cfg.get("kb") or {},
            "only_at_groups": cfg.get("only_at_groups") or [],
            "no_context_groups": cfg.get("no_context_groups") or [],
            "record_at_only_groups": cfg.get("record_at_only_groups") or [],
            "allowed_groups": cfg.get("allowed_groups") or [],
            "intro": cfg.get("intro") or {},
            "agent": {k: v for k, v in (cfg.get("agent") or {}).items()
                      if k in ("max_rounds", "max_calls", "max_sends_per_turn", "loop_mode",
                               "loop_groups", "send_tools")},
            "admin": cfg.get("admin") or {},
        },
        "personas": list_personas(),
        "groups": groups_overview(),
        "stickers": sticker_list(),
        "now": int(time.time()),
    }


# ---------------- HTTP ----------------
def _password():
    try:
        with open(SECRET, encoding="utf-8") as f:
            pw = f.read().strip()
        if pw:
            return pw
    except Exception:
        pass
    pw = base64.b32encode(os.urandom(10)).decode().rstrip("=")
    os.makedirs(os.path.dirname(SECRET), exist_ok=True)
    _atomic_write(SECRET, pw)
    try:
        os.chmod(SECRET, 0o600)
    except Exception:
        pass
    print("[webadmin] 已生成访问密码（%s）：%s" % (SECRET, pw))
    return pw


class H(BaseHTTPRequestHandler):
    server_version = "qqbot-webadmin/1.0"
    pw = ""

    def log_message(self, fmt, *args):
        try:
            with open(os.path.join(PLUGIN_DIR, "data", "webadmin.log"), "a") as f:
                f.write("%s %s %s\n" % (time.strftime("%F %T"), self.address_string(),
                                        fmt % args))
        except Exception:
            pass

    def _auth_ok(self):
        h = self.headers.get("Authorization") or ""
        if not h.startswith("Basic "):
            return False
        try:
            u, p = base64.b64decode(h[6:]).decode().split(":", 1)
        except Exception:
            return False
        return hmac.compare_digest(p, self.pw)

    def _deny(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="qqbot"')
        self.end_headers()
        self.wfile.write("需要密码".encode())

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _raw(self, path, ctype="image/jpeg"):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "max-age=3600")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- GET ----
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if not self._auth_ok():
            return self._deny()
        if u.path in ("/", "/index.html"):
            data = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path == "/api/state":
            return self._json(full_state())
        if u.path == "/api/sticker":
            sid = (q.get("id") or [""])[0]
            if (q.get("raw") or [""])[0] in ("1", "true", "yes"):
                _p2, _ct = sticker_raw(sid)          # 动图原图
                if not _p2:
                    self.send_response(404)
                    self.end_headers()
                    return
                return self._raw(_p2, _ct)
            p = sticker_thumb(sid)
            if not p:
                self.send_response(404)
                self.end_headers()
                return
            return self._raw(p)
        if u.path == "/api/persona":
            name = (q.get("name") or [""])[0]
            txt, p = read_persona(name)
            if txt is None:
                return self._json({"ok": False, "error": "没有这张卡"}, 404)
            return self._json({"ok": True, "name": os.path.basename(p), "text": txt})
        if u.path == "/api/ping":
            return self._json({"ok": True, "t": int(time.time())})
        self.send_response(404)
        self.end_headers()

    # ---- POST ----
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if not self._auth_ok():
            return self._deny()
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
        except Exception as e:
            return self._json({"ok": False, "error": "请求体不是 JSON: %r" % (e,)}, 400)
        try:
            with _LOCK:
                if u.path == "/api/apply":
                    return self._json(apply_changes(body))
                if u.path == "/api/sticker/note":
                    ok, msg = sticker_note(body.get("id"), body.get("desc"), body.get("tags"))
                    return self._json({"ok": ok, "msg": msg})
                if u.path == "/api/sticker/delete":
                    ok, msg = sticker_delete(body.get("id"))
                    return self._json({"ok": ok, "msg": msg})
                if u.path == "/api/persona/name":
                    ok, msg = save_name(body.get("name"), body.get("title"))
                    return self._json({"ok": ok, "title": msg})
                if u.path == "/api/reload":
                    r = subprocess.run([RELOAD_BIN], capture_output=True, text=True, timeout=180)
                    out = (r.stdout or "") + (r.stderr or "")
                    return self._json({"ok": ("已加载" in out or "重载成功" in out),
                                       "tail": out[-400:]})
        except Exception as e:
            return self._json({"ok": False, "error": repr(e)}, 500)
        self.send_response(404)
        self.end_headers()


HTML = r"""<!doctype html><html lang=zh><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=color-scheme content=light>
<title>大肥鱼 · 管理面板</title>
<style>
:root{--bd:#e5e7eb;--bg:#f7f8fa;--fg:#1f2328;--acc:#3b6ef0;--warn:#b45309;color-scheme:light}
html,body{color-scheme:light}
*{box-sizing:border-box}
body{font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:0;background:var(--bg);color:var(--fg)}
header{position:sticky;top:0;background:#fff;border-bottom:1px solid var(--bd);padding:10px 16px;display:flex;gap:12px;align-items:center;z-index:9}
header b{font-size:16px} header .sp{flex:1}
button{border:1px solid var(--bd);background:#fff;border-radius:8px;padding:6px 12px;cursor:pointer}
button.p{border-color:var(--acc);background:var(--acc);color:#fff;font-weight:600}
button.d{border-color:#e5b4b4;color:#b91c1c}
button:disabled{opacity:.5;cursor:default}
nav{display:flex;gap:6px;padding:10px 16px 0}
nav a{padding:6px 12px;border-radius:8px;text-decoration:none;color:#555}
nav a.on{background:#fff;border:1px solid var(--bd);color:#000;font-weight:600}
main{padding:12px 16px 60px}
.card{background:#fff;border:1px solid var(--bd);border-radius:12px;padding:12px;margin:0 0 12px}
.card h3{margin:0 0 8px;font-size:15px}
.grid{display:grid;gap:10px 14px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));margin-top:6px}
.fld{min-width:0}
.fld label{margin:0 0 2px}
.fld input{font-size:12.5px}
.grid.st{grid-template-columns:repeat(auto-fill,minmax(190px,1fr))}
label{display:block;margin:6px 0 2px;color:#555;font-size:13px}
input[type=text],input[type=number],textarea,select{width:100%;border:1px solid var(--bd);border-radius:8px;padding:6px 8px;font:13px/1.5 ui-monospace,Menlo,Consolas,monospace;background:#fff;color:#1f2328;color-scheme:light}
select{-webkit-appearance:menulist;appearance:auto}
select option,select optgroup{background:#fff;color:#1f2328}
select option:checked{background:#dbe7ff;color:#123}
input::placeholder,textarea::placeholder{color:#9aa1a9}
textarea{min-height:120px}
textarea.big{min-height:420px}
code{background:#f1f3f5;padding:0 4px;border-radius:4px;font-size:12px}
.tag{display:inline-block;background:#eef4ff;color:#2a5db0;border-radius:5px;padding:0 6px;margin:2px 4px 0 0;font-size:12px}
.st .card{padding:8px}
.st img{width:100%;height:132px;object-fit:contain;background:#f2f2f2;border-radius:8px}
.st .d{font-weight:600;margin:4px 0 2px;word-break:break-word;font-size:13px}
.st .m{color:#888;font-size:12px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.chip{display:inline-block;border:1px solid var(--bd);background:#fff;border-radius:999px;
  padding:2px 10px;margin:3px 5px 3px 0;cursor:pointer;font-size:12.5px;user-select:none}
.chip:hover{border-color:#9db8f0}
.chip.on{background:#dbe7ff;border-color:#9db8f0;color:#123;font-weight:600}
.chip.dirty{box-shadow:inset 0 -2px 0 #f0b429}
.gitem{padding:8px 0;border-bottom:1px dashed #eee}
.gitem:last-child{border-bottom:0}
.gname{font-weight:600;margin-bottom:2px}
.plist{max-height:190px;overflow:auto;border:1px solid var(--bd);border-radius:8px;padding:6px;background:#fcfcfd}
.pgrid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(340px,1fr))}
.pcard{background:#fff;border:1px solid var(--bd);border-radius:12px;overflow:hidden;transition:box-shadow .15s}
.pcard:hover{box-shadow:0 2px 10px rgba(0,0,0,.06)}
.pcard.open{grid-column:1/-1;box-shadow:0 2px 14px rgba(0,0,0,.08)}
.phead{display:flex;align-items:flex-start;gap:10px;padding:11px 13px;cursor:pointer}
.phead:hover{background:#f8fafc}
.ptitle{font-weight:600;flex:1;word-break:break-word;font-size:14.5px}
.pmeta{color:#6b7280;font-size:12px;margin-top:2px}
.pchev{color:#9aa1a9;font-size:13px;line-height:1.4}
.pbody{display:none;border-top:1px solid #f0f1f3;padding:10px 13px 13px;background:#fcfcfd}
.pcard.open .pbody{display:block}
.pbody textarea{min-height:360px}
.dirtymark{color:#b45309;font-weight:600}
.chk{display:flex;align-items:center;gap:6px;font-size:13px;color:#333;margin:4px 0}
.toast{position:fixed;right:16px;bottom:16px;background:#111;color:#fff;padding:10px 14px;border-radius:10px;max-width:60vw;white-space:pre-wrap;display:none;z-index:99}
.hint{color:#6b7280;font-size:12.5px;margin:2px 0 8px}
.warn{color:var(--warn)}
</style>
<header><b>🐟 大肥鱼 · 管理面板</b><span class=sp></span>
  <span id=stat class=hint></span>
  <button onclick=reloadState()>刷新</button>
  <button class=p id=applyBtn onclick=applyAll()>应用到大肥鱼</button>
</header>
<nav>
  <a href="#" data-t=persona class=on>人格卡</a>
  <a href="#" data-t=group>群行为限制</a>
  <a href="#" data-t=sticker>表情包</a>
  <a href="#" data-t=misc>其它开关</a>
</nav>
<main>
  <section id=t-persona></section>
  <section id=t-group style="display:none"></section>
  <section id=t-sticker style="display:none"></section>
  <section id=t-misc style="display:none"></section>
</main>
<div class=toast id=toast></div>
<script>
let S=null, dirty={config:{}, personas:{}, active:{}};
const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
function toast(m,ms=3200){const t=$('#toast');t.style.display='block';t.textContent=m;clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',ms);}
function markDirty(k,v){dirty.config[k]=v;$('#stat').textContent='有未应用的改动';}
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
  if(!r.ok){throw new Error(await r.text());} return r.json();}
async function reloadState(){S=await api('/api/state');dirty={config:{},personas:{},active:{}};$('#stat').textContent='';render();}
function render(){renderPersona();renderGroup();renderSticker();renderMisc();}
function num(o,k,d){return (o&&o[k]!==undefined&&o[k]!==null)?o[k]:d}
function setVal(sel,val){const e=$(sel);if(e)e.value=val}
function tuningEditor(gid,t){
  const F=[['reply_prob_mult','主动接话概率倍率',t.reply_prob_mult??''],
    ['max_total_per_hour','每小时总上限（主动接话）',t.max_total_per_hour??''],
    ['max_auto_per_hour','主动接话/小时',t.max_auto_per_hour??''],
    ['min_interval_sec','没在对话里冷却(秒)',t.min_interval_sec??''],
    ['max_cont_per_hour','对话延续/小时',t.max_cont_per_hour??''],
    ['min_cont_gap_sec','接着说最少间隔(秒)',t.min_cont_gap_sec??''],
    ['cont_window_sec','延续窗口(秒)',t.cont_window_sec??''],
    ['max_engaged_streak','连续接话上限(次)',t.max_engaged_streak??''],
    ['engaged_rest_sec','连击用完后冷却(秒)',t.engaged_rest_sec??'']];
  return F.map(([k,label])=>`<div class=fld><label>${label} <code>${k}</code></label><input type=text data-g='${gid}' data-k='${k}' value="${t[k]??''}" placeholder='留空=用全局值' oninput="onTune('${gid}','${k}',this.value)"></div>`).join('');
}
function onTune(gid,k,v){dirty.config.group_tuning=dirty.config.group_tuning||{};
  const g=dirty.config.group_tuning[gid]=dirty.config.group_tuning[gid]||{};
  if(v=== ''){delete g[k];} else {g[k]=isNaN(Number(v))?v:Number(v);} $('#stat').textContent='有未应用的改动';}
let _expanded=new Set();      // 展开的卡
let _curCard='';
function pcardHtml(c){
  const open=_expanded.has(c.name);
  const unsaved=(dirty.personas||{})[c.name]!==undefined;
  const badges=[c.legacy?'旧版':'', c.helper?'辅助':'', c.renamed?'已命名':''].filter(Boolean);
  return `<div class="pcard ${open?'open':''}" data-card="${c.name}">
    <div class=phead onclick="toggleCard('${c.name}')">
      <div class=ptitle>${c.title}
        <div class=pmeta>${c.name} · ${c.chars} 字${badges.length?' · '+badges.join(' · '):''}
        ${unsaved?'<span class=dirtymark>● 有未保存改动</span>':''}</div></div>
      <div class=pchev>${open?'▾ 收起':'▸ 展开'}</div>
    </div>
    <div class=pbody>
      <div class=row style="margin:0 0 8px">
        <label style="margin:0">显示名</label>
        <input type=text class=pname style="max-width:260px" value="${c.renamed?c.title:''}" placeholder="（留空 = 用文件名 ${c.name}）">
        <button onclick="event.stopPropagation();saveNameFor('${c.name}',this)">保存名称</button>
        <span class=hint>只是面板里好认，不写进卡内容</span>
      </div>
      <textarea class="big ptext" data-name="${c.name}" onclick="event.stopPropagation()"
        oninput="touchCardEl(this)" placeholder="（展开后自动加载内容；改完点右上角『应用到大肥鱼』生效）"></textarea>
      <div class=hint style="margin-top:6px">文件：<code>${c.legacy?'prompts/':'prompts/personas/'}${c.name}</code>
        —— 改动要点右上角「应用」；改名是立刻生效的。</div>
    </div></div>`;
}
function renderPersona(){
  const all=S.personas||[];
  const cards=all.filter(c=>!c.helper), helpers=all.filter(c=>c.helper);
  $('#t-persona').innerHTML=`
  <div class=card><h3>全部人格卡 <span class=hint>共 ${all.length} 张（${cards.length} 张可用 + ${helpers.length} 张辅助）</span>
      <button style="float:right" onclick=newPersona()>新建人格卡</button></h3>
    <p class=hint>点卡片展开：可以改显示名、改内容。卡片默认收起。</p>
    <div class=pgrid>${cards.map(pcardHtml).join('')}</div>
    ${helpers.length?`<p class=hint style="margin-top:14px">辅助文件（自动套在所有卡后面 / 新建卡的模板，<b>不要挂到群上</b>）：</p>
      <div class=pgrid>${helpers.map(pcardHtml).join('')}</div>`:''}
  </div>`;
  _expanded.forEach(n=>loadCardInto(n));
}
function toggleCard(name){
  _expanded.has(name)?_expanded.delete(name):_expanded.add(name);
  renderPersona();
}
async function loadCardInto(name){
  const ta=document.querySelector(`.ptext[data-name="${name}"]`);
  if(!ta||ta.dataset.loaded)return;
  try{
    const r=await api('/api/persona?name='+encodeURIComponent(name));
    if((dirty.personas||{})[name]!==undefined){ta.value=dirty.personas[name];}
    else{ta.value=r.text;}
    ta.dataset.loaded='1';
  }catch(e){ta.value='（读不到：'+e.message+'）';}
}
function touchCardEl(ta){
  if(!ta||!ta.dataset.name)return;
  dirty.personas[ta.dataset.name]=ta.value;
  const card=ta.closest('.pcard');
  if(card&&!card.querySelector('.dirtymark')){
    const m=card.querySelector('.pmeta'); if(m)m.insertAdjacentHTML('beforeend',' <span class=dirtymark>● 有未保存改动</span>');
  }
  $('#stat').textContent='有未应用的改动';
}
function touchCard(){const ta=document.querySelector('.ptext');if(ta)touchCardEl(ta);}
async function saveNameFor(name,btn){
  const inp=btn.closest('.pbody').querySelector('.pname');
  const r=await api('/api/persona/name',{name:name,title:inp.value});
  if(r.ok){toast('名称已改：'+r.title);
    try{const c=(S.personas||[]).find(x=>x.name===name);
        if(c){c.title=r.title;c.renamed=!!(inp.value||'').trim();} }catch(e){}
    renderPersona();
  } else {toast('改失败：'+(r.error||r.title));}
}
async function pickPersona(name){ return loadCardInto(name); }      // 兼容旧调用
async function loadPersona(name){ return loadCardInto(name); }
async function saveName(){const ta=document.querySelector('.ptext');if(ta)return saveNameFor(ta.dataset.name,ta);}
function newPersona(){const n=prompt('新人格卡文件名（字母数字-_，.txt 结尾，例：csgo_friend.txt）');if(!n)return;
  _expanded.add(n); dirty.personas[n]='【你是谁】\n你是「大肥鱼」…\n';
  S.personas=S.personas||[]; S.personas.push({name:n,title:n,chars:0,legacy:false,helper:false});
  renderPersona();
  setTimeout(()=>{const ta=document.querySelector(`.ptext[data-name="${n}"]`);
    if(ta){ta.value=dirty.personas[n];ta.dataset.loaded='1';}},50);
}
function onMap(gid,v,el){
  const m=dirty.config.prompt_by_group=dirty.config.prompt_by_group||{};
  if(v){m[gid]=v;}else{m[gid]=null;}
  try{
    const row=(el&&el.parentElement)?el.parentElement
      :(document.querySelector(`.gitem[data-gid="${gid}"]`)
        ||document.querySelector(`.chip[data-gid="${gid}"]`));
    if(row&&row.querySelectorAll){
      row.querySelectorAll('.chip').forEach(c=>c.classList.remove('on'));
      if(el)el.classList.add('on');
      let tag=row.querySelector('.dtag');
      if(!tag&&row.appendChild){tag=document.createElement('span');tag.className='dtag hint';
        tag.textContent='· 已改，待应用';row.appendChild(tag);}
      else if(tag){tag.textContent='· 已改，待应用';}
    }
  }catch(e){}
  $('#stat').textContent='有未应用的改动';
}
function mapChips(g){
  const cards=(S.personas||[]).filter(c=>!c.helper);
  const pend=dirty.config.prompt_by_group||{};
  const cur=(Object.prototype.hasOwnProperty.call(pend,g.gid))?((pend[g.gid]===null||pend[g.gid]==='')?'':pend[g.gid]):(g.persona||'');
  const chip=(name,label,tip)=>`<span class="chip ${cur===name?'on':''}" title="${tip||label}" onclick="onMap('${g.gid}','${name}',this)">${label}</span>`;
  return [chip('', '默认卡', '走 prompts/system_prompt.txt')]
    .concat(cards.map(c=>chip(c.name, c.title+(c.legacy?'（旧版）':''), c.name))).join('');
}
function renderGroup(){
  $('#t-group').innerHTML=S.groups.map(g=>`<div class=card>
    <h3>${g.name?g.name+' ':'（未知群名）'}<code>${g.gid}</code>`
      +`${g.note?` <span class=tag>备注：${g.note}</span>`:''}
      <span class=hint>${g.members?g.members+'人 · ':''}${g.in_bot?'她在群里':'<b style="color:#b45309">她已不在这个群</b>'}`
      +` · ${g.in_allowlist?(g.active?'激活中':'未激活'):'<b style="color:#b45309">未加入白名单，她不理会</b>'}</span></h3>
    <div class=row>
      <label class=chk><input type=checkbox ${g.in_allowlist?'checked':''} onchange="onAllow('${g.gid}',this.checked)"> 服务这个群（加入白名单）</label>
      <label class=chk><input type=checkbox ${g.active?'checked':''} onchange="onActive('${g.gid}',this.checked)"> 激活（她在这个群说话）</label>
      <label class=chk><input type=checkbox ${g.only_at?'checked':''} onchange="onList('only_at_groups','${g.gid}',this.checked)"> 只在被 @ 时回</label>
      <label class=chk><input type=checkbox ${g.no_context?'checked':''} onchange="onList('no_context_groups','${g.gid}',this.checked)"> 不进上下文</label>
      <label class=chk><input type=checkbox ${g.record_at_only?'checked':''} onchange="onList('record_at_only_groups','${g.gid}',this.checked)"> 只记 @ 她的</label>
      <label class=chk><input type=checkbox ${g.no_sticker_send?'checked':''} onchange="onStkList('send_exclude_groups','${g.gid}',this.checked)"> 不发表情</label>
      <label class=chk><input type=checkbox ${g.no_sticker_collect?'checked':''} onchange="onStkList('collect_exclude_groups','${g.gid}',this.checked)"> 不收表情</label>
    </div>
    <div class=row style="margin-top:6px"><span class=hint style="margin-right:4px">用哪张人格卡：</span>${mapChips(g)}</div>
    <div class=row><label style="margin:0">群备注名（只影响面板显示）</label>
      <input type=text style="max-width:240px" value="${g.note||''}" placeholder="例：闪电群" oninput="onNote('${g.gid}',this.value)"></div>
    <label>资料库域（kb.scope_by_group，留空=不挂）</label>
    <input type=text value="${g.kb_scope||''}" oninput="onScope('${g.gid}',this.value)">
    <div class=grid>${tuningEditor(g.gid,g.tuning||{})}</div>
  </div>`).join('');
}
function onActive(gid,v){dirty.active[gid]=v;$('#stat').textContent='有未应用的改动';}
function onAllow(gid,v){const a=new Set((dirty.config.allowed_groups||S.config.allowed_groups||[]));
  v?a.add(gid):a.delete(gid);dirty.config.allowed_groups=[...a];$('#stat').textContent='有未应用的改动';}
function onNote(gid,v){const ad=Object.assign({},dirty.config.admin||S.config.admin||{});
  const m=Object.assign({},ad.group_notes||{}); if(v.trim()){m[gid]=v.trim();}else{delete m[gid];}
  ad.group_notes=m;dirty.config.admin=ad;$('#stat').textContent='有未应用的改动';}
function onList(key,gid,v){const a=new Set(S.config[key]||[]);v?a.add(gid):a.delete(gid);dirty.config[key]=[...a];$('#stat').textContent='有未应用的改动';}
function onStkList(key,gid,v){const st=Object.assign({},dirty.config.stickers||S.config.stickers||{});
  const a=new Set(st[key]||[]);v?a.add(gid):a.delete(gid);st[key]=[...a];dirty.config.stickers=st;$('#stat').textContent='有未应用的改动';}
function onScope(gid,v){const kb=Object.assign({},dirty.config.kb||S.config.kb||{});const m=Object.assign({},kb.scope_by_group||{});
  if(v.trim()){m[gid]=v.trim();}else{delete m[gid];}kb.scope_by_group=m;dirty.config.kb=kb;$('#stat').textContent='有未应用的改动';}
function renderSticker(){
  const pref=new Set((S.config.stickers&&S.config.stickers.prefer_ids)||[]);
  $('#t-sticker').innerHTML=`<div class=card><h3>全局（收藏/发送规则）</h3>
    <div class=row><label class=chk><input type=checkbox ${S.config.stickers.collect_say_enabled?'checked':''} onchange="onStk('collect_say_enabled',this.checked)"> 收藏表情包时说话</label>
      <label class=chk><input type=checkbox ${S.config.stickers.send_private?'checked':''} onchange="onStk('send_private',this.checked)"> 私聊也能发表情</label>
      <label class=chk><input type=checkbox ${S.config.stickers.collect_private?'checked':''} onchange="onStk('collect_private',this.checked)"> 私聊也收图</label></div>
    <div class=row><label>单文件上限(MB)</label><input type=text value="${S.config.stickers.max_file_mb??''}" oninput="onStk('max_file_mb',Number(this.value))">
      <label>库里最多存</label><input type=text value="${S.config.stickers.max_store??''}" oninput="onStk('max_store',Number(this.value))">
      <label>每小时最多发</label><input type=text value="${S.config.stickers.max_per_hour??''}" oninput="onStk('max_per_hour',Number(this.value))"></div>
    <p class=hint>共 ${S.stickers.length} 张。点缩略图上的「形象图」可以把这张设成"优先露脸"（prefer_ids）。</p></div>
  <div class=row style="margin:-4px 0 8px">
    <button onclick="playAllAnims(true)">▶ 全部播放动图</button>
    <button onclick="playAllAnims(false)">⏸ 全部用静态缩略图</button>
    <span class=hint>动图（${S.stickers.filter(x=>x.animated).length} 张）默认显示第一帧，点图即播放；一进页面全播会拖慢加载。</span>
  </div>
  <div class="grid st">${S.stickers.map(x=>`<div class=card>
      <img src="/api/sticker?id=${x.id}" loading=lazy title="${x.animated?'点一下播放动图':''}"
        ${x.animated?`onclick="toggleAnim('${x.id}',this)"`:''}>
      <div class=d${x.desc?'':' style="color:#b45309"'}>${x.desc||'（没有备注 —— 建议写"怎么用"）'}</div>
      <div class=m>${x.id} · 用过 ${x.used}${x.face_pushed?' · 已推面板':''}${x.animated?` · <b style="color:#2a5db0">动图 ${(x.fsize/1048576).toFixed(1)}MB</b>`:''}</div>
      <div>${x.tags.map(t=>`<span class=tag>${t}</span>`).join('')}</div>
      <div class=row style="margin-top:6px">
        <button onclick="editNote('${x.id}')">改备注</button>
        <button onclick="togglePref('${x.id}')">${pref.has(x.id)?'取消形象图':'设为形象图'}</button>
        <button class=d onclick="delSticker('${x.id}')">删除</button>
      </div></div>`).join('')}</div>`;
}
function onStk(k,v){const st=Object.assign({},dirty.config.stickers||S.config.stickers||{});st[k]=v;dirty.config.stickers=st;$('#stat').textContent='有未应用的改动';}
function editNote(id){const x=S.stickers.find(s=>s.id===id);const d=prompt('这张图的备注（写"怎么用"，不是"长什么样"）',x.desc||'');if(d===null)return;
  const t=prompt('标签（逗号分隔）',(x.tags||[]).join(','));if(t===null)return;
  api('/api/sticker/note',{id:id,desc:d,tags:t.split(',').map(s=>s.trim()).filter(Boolean)}).then(r=>{
    if(r.ok){toast('已保存，她会立刻用上新备注');reloadState();}else{toast('失败：'+r.msg);}});}
function delSticker(id){if(!confirm('从本地库删掉 '+id+'？（QQ 面板里的还在，可用 mirror 同步）'))return;
  api('/api/sticker/delete',{id:id}).then(r=>{toast(r.ok?r.msg:'失败：'+r.msg);reloadState();});}
function toggleAnim(id,img){
  const raw=img.dataset.raw==='1';
  img.src='/api/sticker?id='+id+(raw?'':'&raw=1');
  img.dataset.raw=raw?'0':'1';
  img.title=raw?'点一下播放动图':'点一下回到静态';
}
function playAllAnims(on){
  document.querySelectorAll('.grid.st img').forEach(img=>{
    const card=img.closest('.card'); if(!card)return;
    const m=(card.querySelector('.m')||{}).textContent||'';
    if(m.indexOf('动图')>=0){ img.src=img.src.replace(/&raw=1$/,'')+(on?'&raw=1':''); }
  });
}
function togglePref(id){const cur=new Set((S.config.stickers&&S.config.stickers.prefer_ids)||[]);cur.has(id)?cur.delete(id):cur.add(id);
  const st=Object.assign({},dirty.config.stickers||S.config.stickers||{});st.prefer_ids=[...cur];dirty.config.stickers=st;$('#stat').textContent='有未应用的改动';renderSticker();}
function renderMisc(){
  const p=S.config.perception||{},s=S.config.search||{},a=S.config.agent||{},i=S.config.intro||{};
  $('#t-misc').innerHTML=`<div class=card><h3>感知 / 搜索 / 轮数</h3>
    <div class=row><label class=chk><input type=checkbox ${p.enabled!==false?'checked':''} onchange="onDeep('perception','enabled',this.checked)"> 环境感知（时间/气氛/日期）</label>
      <label>感知字数上限</label><input type=text value="${p.max_chars??420}" oninput="onDeep('perception','max_chars',Number(this.value))"></div>
    <div class=row><label class=chk><input type=checkbox ${s.enabled!==false?'checked':''} onchange="onDeep('search','enabled',this.checked)"> 允许联网搜索</label>
      <label class=chk><input type=checkbox ${s.smart_judge!==false?'checked':''} onchange="onDeep('search','smart_judge',this.checked)"> 先判再查</label>
      <label>每小时最多查</label><input type=text value="${s.max_per_hour??20}" oninput="onDeep('search','max_per_hour',Number(this.value))"></div>
    <div class=row><label>一轮最多几轮对话</label><input type=text value="${a.max_rounds??5}" oninput="onDeep('agent','max_rounds',Number(this.value))">
      <label>单轮最多发几条</label><input type=text value="${a.max_sends_per_turn??8}" oninput="onDeep('agent','max_sends_per_turn',Number(this.value))">
      <label>工具调用上限</label><input type=text value="${a.max_calls??6}" oninput="onDeep('agent','max_calls',Number(this.value))"></div>
    <div class=row><label class=chk><input type=checkbox ${i.enabled!==false?'checked':''} onchange="onDeep('intro','enabled',this.checked)"> 自我介绍段</label>
      <label class=chk><input type=checkbox ${S.config.admin.enabled!==false?'checked':''} onchange="onDeep('admin','enabled',this.checked)"> 私聊只读后台</label></div>
  </div>
  <div class=card><h3>群备注名</h3>
    <p class=hint>已经挪到「群行为限制」页，每个群下面直接改（只影响面板里怎么显示，不影响她）。</p></div>`;
}
function onDeep(top,k,v){const o=Object.assign({},dirty.config[top]||S.config[top]||{});o[k]=v;dirty.config[top]=o;$('#stat').textContent='有未应用的改动';}
async function applyAll(){
  const b=$('#applyBtn');
  try{
    const hasP=Object.keys(dirty.personas||{}).length,
          hasC=Object.keys(dirty.config||{}).length,
          hasA=Object.keys(dirty.active||{}).length;
    if(!hasP&&!hasC&&!hasA){toast('没有改动');return;}
    b.disabled=true;b.textContent='应用中…';
    const r=await api('/api/apply',{config:dirty.config,personas:dirty.personas,
                                    active:dirty.active,reload:true});
    if(!r.ok){toast('没有应用：\n'+((r.errors||[r.error||'未知错误']).join('\n')),7000);}
    else{
      const t=(r.logs||[]).join('\n')+(r.reload?('\n重载：'+(r.reload.ok?'成功':(r.reload.tail||''))):'');
      toast('已应用：\n'+t,7000);
      await reloadState();
    }
  }catch(e){
    toast('出错：'+(e&&e.message?e.message:e),8000);
  }finally{
    b.disabled=false;b.textContent='应用到大肥鱼';
  }
}
$$('nav a').forEach(a=>a.onclick=e=>{e.preventDefault();$$('nav a').forEach(x=>x.classList.remove('on'));a.classList.add('on');
  ['persona','group','sticker','misc'].forEach(t=>$('#t-'+t).style.display=(t===a.dataset.t)?'':'none');});
reloadState().catch(e=>toast('加载失败：'+e.message));
</script></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8977)
    ap.add_argument("--password", default="")
    a = ap.parse_args()
    H.pw = a.password or _password()
    srv = ThreadingHTTPServer((a.host, a.port), H)
    print("[webadmin] listening on http://%s:%d (基本认证：任意用户名 + 上面那个密码)" % (a.host, a.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
