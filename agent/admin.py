# -*- coding: utf-8 -*-
"""只读后台（主人私聊专用）：问一句，回一段现成的状态，不花 token、不改任何东西。

设计：
- `route(text, cfg, plugin_dir)` 是**纯函数**：只读文件/配置，返回 (handled, parts)。
- 只有主人（admin.owner_ids，缺省用 activation.owner_ids）在**私聊**里能用；
  其他人私聊、所有群聊一律不匹配（回到正常聊天）。
- 命令靠关键词匹配，说得口语一点也能认（「列出一下你现在的所有人格面具」）。
- 回复按 QQ 短消息切分：每条 ≤300 字，最多 4 条。
"""
import json
import os
import re
import time

MAX_PART = 300
MAX_PARTS = 4


# ---------------- 小工具 ----------------
def _read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _plugins_dir(plugin_dir: str) -> str:
    return plugin_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pack(title: str, lines: list) -> list:
    """把行拼成若干条 ≤MAX_PART 的消息（首条带标题）。"""
    out, cur = [], ""
    for ln in lines:
        ln = str(ln)
        if len(ln) > MAX_PART - 20:
            ln = ln[: MAX_PART - 20] + "…"
        if cur and len(cur) + len(ln) + 1 > MAX_PART - 20:
            out.append(cur)
            cur = ln
        else:
            cur = (cur + "\n" + ln) if cur else ln
    if cur:
        out.append(cur)
    out = out[:MAX_PARTS]
    if out:
        out[0] = "【后台·只读｜%s】\n%s" % (title, out[0])
    return out


def _short(t: str, n: int) -> str:
    t = " ".join(str(t or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _cnt(v) -> str:
    """索引里的 files/chunks 各版本不同（int 或列表），统一成数字。"""
    if isinstance(v, (list, dict)):
        return str(len(v))
    return str(v if v is not None else "?")


def _now_str(ts) -> str:
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(ts)))
    except Exception:
        return "?"


# ---------------- 各命令 ----------------
def _personas(cfg: dict, pdir: str, arg: str) -> list:
    """所有人格面具 + 每个群在用哪张。"""
    d = os.path.join(pdir, "prompts", "personas")
    rows = []
    try:
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".txt") or fn.startswith("_"):
                continue
            p = os.path.join(d, fn)
            try:
                txt = open(p, encoding="utf-8").read()
            except Exception:
                txt = ""
            first = ""
            for ln in txt.splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    first = _short(ln, 40)
                    break
            _users = [str(g) for g, v in (cfg.get("prompt_by_group") or {}).items()
                      if os.path.basename(str(v)) == fn]
            for g, v in (_read_json(os.path.join(pdir, "data", "group_registry.json"),
                                    {}) or {}).items():
                if os.path.basename(str((v or {}).get("persona") or "")) == fn and str(g) not in _users:
                    _users.append("%s(自动建档)" % g)
            rows.append((fn, len(txt), first, _users))
    except Exception as e:
        return _pack("人格面具", ["读不到 personas 目录：%r" % (e,)])
    # 群 → 卡 的完整映射（含 prompt.prompt_by_group / persona_by_group）
    def _map_of(*paths):
        out = {}
        for pth in paths:
            cur = cfg
            for seg in pth:
                cur = (cur or {}).get(seg) or {}
            if isinstance(cur, dict):
                out.update({str(k): str(v) for k, v in cur.items()})
        return out
    gmap = _map_of(["prompt_by_group"])
    gmap.update({k: v for k, v in _map_of(["prompt", "prompt_by_group"]).items()})
    notes = (cfg.get("admin") or {}).get("group_notes") or {}
    lines = ["共 %d 张（prompts/personas/）：" % len(rows)]
    for fn, n, first, users in rows:
        _u = ("→ %s" % "、".join("%s%s" % (notes.get(u, ""), u) for u in users)) if users else "→ 没群在用"
        lines.append("· %s（%d 字）%s\n   %s" % (fn, n, _u, first or "（没描述）"))
    # 旧版卡（prompts/system_prompt_*.txt）也列出来，那才是最常用的几张
    leg = []
    try:
        _pd = os.path.join(pdir, "prompts")
        _files = [f for f in sorted(os.listdir(_pd))
                  if f.startswith("system_prompt") and f.endswith(".txt")]
        for fn in _files:
            try:
                _n = len(open(os.path.join(_pd, fn), encoding="utf-8").read())
            except Exception:
                _n = 0
            _u = [str(g) for g, v in gmap.items() if os.path.basename(str(v)) == fn]
            leg.append("· %s（%d 字）→ %s" % (fn, _n,
                                              "、".join("%s%s" % (notes.get(u, ""), u) for u in _u)
                                              or "没群在用"))
    except Exception:
        leg = []
    if leg:
        lines.append("旧版卡（prompts/）：")
        lines.extend(leg)
    if gmap:
        lines.append("各群在用：")
        for g, v in sorted(gmap.items()):
            lines.append("· %s%s → %s" % (notes.get(str(g), ""), g, os.path.basename(str(v))))
    else:
        lines.append("各群在用：没配 prompt_by_group（都走默认卡）")
    try:
        import promptlib
        _dflt, _src = promptlib.persona.load_persona(pdir, cfg, "999999999", False)
        lines.append("默认卡（没配的群）：%s" % (os.path.basename(str(_src)) or "内置"))
    except Exception:
        pass
    return _pack("人格面具", lines)


def _groups(cfg: dict, pdir: str, arg: str) -> list:
    """群/会话总览：人格、激活、是否只@、KB 域、表情开关。"""
    notes = (cfg.get("admin") or {}).get("group_notes") or {}
    gmap = {str(k): str(v) for k, v in (cfg.get("prompt_by_group") or {}).items()}
    gmap.update({str(k): str(v) for k, v in
                 ((cfg.get("prompt") or {}).get("prompt_by_group") or {}).items()})
    act = (_read_json(os.path.join(pdir, "data", "agent_active.json"), {}) or {}).get("groups") or {}
    reg = _read_json(os.path.join(pdir, "data", "group_registry.json"), {}) or {}
    for _g, _v in reg.items():
        _p = os.path.basename(str((_v or {}).get("persona") or ""))
        if _p and str(_g) not in gmap:
            gmap[str(_g)] = _p
    gids = set(gmap) | set(act) | {str(x) for x in (cfg.get("allowed_groups") or [])} | set(reg)
    only_at = {str(x) for x in (cfg.get("only_at_groups") or [])}
    nocontext = {str(x) for x in (cfg.get("no_context_groups") or [])}
    kbmap = {str(k): str(v) for k, v in ((cfg.get("kb") or {}).get("scope_by_group") or {}).items()}
    stk = cfg.get("stickers") or {}
    stk_off = {str(x) for x in (stk.get("send_exclude_groups") or [])}
    stk_nocol = {str(x) for x in (stk.get("collect_exclude_groups") or [])}
    lines = []
    for g in sorted(gids):
        a = act.get(g) or {}
        _priv = g.startswith("p:") or g in ("private", "")
        _f = ["私聊"] if _priv else (["激活"] if a.get("on") else ["未激活"])
        if g in only_at:
            _f.append("只@")
        if g in nocontext:
            _f.append("不进上下文")
        if kbmap.get(g):
            _f.append("KB:%s" % kbmap[g])
        if g in stk_off:
            _f.append("不发表情")
        if g in stk_nocol:
            _f.append("不收表情")
        lines.append("· %s%s ｜%s｜人格 %s" % (notes.get(g, ""), g, "、".join(_f),
                                              os.path.basename(gmap.get(g, "默认"))))
    lines.append("白名单 allowed_groups：%s" % ("、".join(str(x) for x in
                                                    (cfg.get("allowed_groups") or [])) or "（空=全放行）"))
    return _pack("群/会话", lines)


def _memory(cfg: dict, pdir: str, arg: str) -> list:
    """群印象 / 人物档案：不点名就列概览，点名（群号）就给全文。"""
    from memory import store
    mdir = os.path.join(pdir, "data", "memory")
    prof = os.path.join(mdir, "group_profile")
    mem = os.path.join(mdir, "members")
    gid = ""
    for w in re.findall(r"\d{5,12}", str(arg or "")):
        gid = w
        break
    if gid:
        txt = store.read_profile(gid)
        if not txt:
            return _pack("记忆", ["群 %s 没有群印象文件" % gid])
        lines = ["群 %s 群印象（%d 字）：" % (gid, len(txt))] + [
            "  " + ln for ln in txt.splitlines() if ln.strip()]
        md = store.read_members(gid).get("members") or {}
        lines.append("人物档案 %d 人：%s" % (len(md), "、".join(
            str(v.get("name") or k) for k, v in sorted(md.items(),
                                                       key=lambda kv: -int(kv[1].get("msg_count") or 0)))))
        return _pack("记忆 %s" % gid, lines)
    lines = []
    try:
        for fn in sorted(os.listdir(prof)):
            if not fn.endswith(".md"):
                continue
            g = fn[:-3]
            p = os.path.join(prof, fn)
            txt = store.read_profile(g)
            lines.append("· %s 群印象 %d 字（%s 更新）" % (g, len(txt), _now_str(os.path.getmtime(p))))
    except Exception:
        pass
    try:
        for fn in sorted(os.listdir(mem)):
            if not fn.endswith(".json"):
                continue
            g = fn[:-5]
            md = (_read_json(os.path.join(mem, fn), {}) or {}).get("members") or {}
            lines.append("· %s 人物档案 %d 人" % (g, len(md)))
    except Exception:
        pass
    if not lines:
        lines = ["还没有任何记忆产物（memory.enabled=%s）" % ((cfg.get("memory") or {}).get("enabled"))]
    lines.append("看某个群全文：说「记忆 <群号>」")
    return _pack("记忆概览", lines)


def _stickers(cfg: dict, pdir: str, arg: str) -> list:
    import stickers as S
    it = S.load()
    st = S.stats()
    top = sorted([x for x in it if x.get("id")],
                 key=lambda x: -int(x.get("used") or 0))[:6]
    pf = cfg.get("stickers") or {}
    lines = ["共 %d 张 / %d MB" % (st.get("count", 0),
                                  int((st.get("bytes") or 0) / 1048576)),
             "已推面板 %d 张，未推 %d 张" % (sum(1 for x in it if x.get("face_pushed")),
                                            sum(1 for x in it if not x.get("face_pushed"))),
             "形象图优先（prefer_ids）%d 张，加分 %s" % (len(pf.get("prefer_ids") or []),
                                                        pf.get("prefer_boost")),
             "用在：%s" % ("、".join(str(x) for x in (pf.get("send_exclude_groups") or [])) or
                           "所有群"),
             "用得最多的："]
    for x in top:
        lines.append("· %s《%s》%d 次" % (x.get("id"), _short(x.get("desc"), 14), int(x.get("used") or 0)))
    return _pack("表情包", lines)


def _kb(cfg: dict, pdir: str, arg: str) -> list:
    root = os.path.join(pdir, "data", "kb")
    lines = []
    top = _read_json(os.path.join(root, "_index.json"), {}) or {}
    if top:
        lines.append("· 默认域：%s 文件 / %s 块（建于 %s）"
                     % (_cnt(top.get("files")), _cnt(top.get("chunks")),
                        _now_str(top.get("built_at"))))
    try:
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if not os.path.isdir(d):
                continue
            idx = _read_json(os.path.join(d, "_index.json"), {}) or {}
            if idx:
                lines.append("· 域 %s：%s 文件 / %s 块（建于 %s）"
                             % (name, _cnt(idx.get("files")), _cnt(idx.get("chunks")),
                                _now_str(idx.get("built_at"))))
    except Exception:
        pass
    kc = cfg.get("kb") or {}
    if not lines:
        lines.append("（还没有任何索引）")
    lines.append("注入：top_k=%s min_score=%s ≤%s 字" % (kc.get("top_k"), kc.get("min_score"),
                                                        kc.get("max_chars")))
    lines.append("按群分域：%s" % (json.dumps(kc.get("scope_by_group") or {}, ensure_ascii=False)))
    return _pack("资料库", lines)


def _pending(cfg: dict, pdir: str, arg: str) -> list:
    p = _read_json(os.path.join(pdir, "data", "agent_pending.json"), {}) or {}
    w = _read_json(os.path.join(pdir, "data", "agent_wake.json"), {}) or {}
    lines = []
    if p:
        for k, v in p.items():
            lines.append("· %s：%s（原话：%s）" % (k, _short((v or {}).get("p"), 30),
                                                 _short((v or {}).get("q"), 30)))
    else:
        lines.append("· 没有待跟进的事")
    if w:
        lines.append("定时唤醒：")
        for k, v in (w.items() if isinstance(w, dict) else []):
            lines.append("· %s → %s" % (k, _short(json.dumps(v, ensure_ascii=False), 90)))
    else:
        lines.append("没有排队的唤醒")
    return _pack("待办", lines)


def _skills(cfg: dict, pdir: str, arg: str) -> list:
    try:
        import promptlib
        rows = promptlib.skills.discover(pdir) or []
    except Exception as e:
        return _pack("技能", ["读技能失败：%r" % (e,)])
    lines = ["共 %d 个（skills/，她按需 use_skill 展开）：" % len(rows)]
    for r in rows:
        nm = r.get("name") if isinstance(r, dict) else str(r)
        ds = (r or {}).get("desc") if isinstance(r, dict) else ""
        lines.append("· %s：%s" % (nm, _short(ds, 50)))
    return _pack("技能", lines)


def _faults(cfg: dict, pdir: str, arg: str) -> list:
    d = _read_json(os.path.join(pdir, "data", "agent_faults.json"), {}) or {}
    c = d.get("counts") or {}
    lines = ["更新于 %s" % _now_str(d.get("updated"))]
    for k, v in sorted(c.items(), key=lambda kv: -int(kv[1] or 0)):
        lines.append("· %s：%s" % (k, v))
    if not c:
        lines.append("· 没记录到故障计数")
    return _pack("故障计数", lines)


def _models(cfg: dict, pdir: str, arg: str) -> list:
    try:
        from agent import llm as _L
        llm = _L.provider_cfg(cfg)
    except Exception:
        llm = dict(((cfg.get("agent") or {}).get("llm") or {}))
    mem = ((cfg.get("memory") or {}).get("provider") or {})
    sc = cfg.get("search") or {}
    vc = cfg.get("stickers") or {}
    lines = ["主对话：%s @ %s" % (llm.get("model") or "(默认)", _short(llm.get("base_url"), 40)),
             "看图：%s" % (llm.get("vision_model") or "(默认)"),
             "记忆批处理：%s" % (mem.get("model") or "(没配)"),
             "搜索判定：%s" % (sc.get("judge_model") or "自动/规则"),
             "搜索：%s（每次 %s 条，每小时 ≤%s 次）" % (sc.get("provider") or "auto",
                                                    sc.get("count"), sc.get("max_per_hour")),
             "表情识图：%s" % (vc.get("vision_model") or "(默认)"),
             "轮数上限 %s / 单轮最多发 %s 条 / 工具调用 %s 次"
             % (((cfg.get("agent") or {}).get("max_rounds")),
                ((cfg.get("agent") or {}).get("max_sends_per_turn")),
                ((cfg.get("agent") or {}).get("max_calls")))]
    return _pack("模型", lines)


def _config_get(cfg: dict, arg: str) -> list:
    path = str(arg or "").strip().strip("：: ")
    path = re.sub(r"^(看一下|看看|查一下|查看|读取|参数|配置)\s*", "", path).strip()
    if not path:
        return _pack("配置", ["顶层配置项（共 %d）：" % len(cfg),
                             "、".join(sorted(cfg.keys())),
                             "看某一项：说「参数 agent.max_rounds」"])
    cur, ok = cfg, True
    for seg in path.split("."):
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            ok = False
            break
    if not ok:
        return _pack("配置", ["找不到配置项 %s" % path])
    txt = json.dumps(cur, ensure_ascii=False, indent=1) if isinstance(cur, (dict, list)) else str(cur)
    lines = ["%s = %s" % (path, _short(txt, 240))]
    return _pack("配置", lines)


def _overview(cfg: dict, pdir: str, arg: str) -> list:
    act = (_read_json(os.path.join(pdir, "data", "agent_active.json"), {}) or {}).get("groups") or {}
    on = [g for g, v in act.items() if (v or {}).get("on")]
    p = _read_json(os.path.join(pdir, "data", "agent_pending.json"), {}) or {}
    try:
        import stickers as S
        st = S.stats()
    except Exception:
        st = {}
    lines = ["激活的群 %d 个：%s" % (len(on), "、".join(sorted(on)) or "无"),
             "人格卡 %d 张，群配置 %d 个"
             % (len([x for x in os.listdir(os.path.join(pdir, "prompts", "personas"))
                     if x.endswith(".txt") and not x.startswith("_")]) if
                os.path.isdir(os.path.join(pdir, "prompts", "personas")) else 0,
                len(cfg.get("prompt_by_group") or {})),
             "表情包 %d 张 / 待跟进 %d 件" % (st.get("count", 0), len(p)),
             "资料库域：%s" % ("、".join(sorted([d for d in os.listdir(os.path.join(pdir, "data", "kb"))
                                             if os.path.isdir(os.path.join(pdir, "data", "kb", d))])
                                          ) or "无")]
    return _pack("总览", lines)


HELP = """问哪方面就说哪方面的词，我照着现成数据念给你（只读，不改任何东西）：
· 人格面具 / 人格卡 → 所有面具 + 每个群在用哪张
· 群 / 会话 → 各群人格、激活、只@、KB、表情开关
· 记忆 / 印象 → 群印象与人物档案（「记忆 869622030」看全文）
· 表情包 → 数量、推送情况、用得最多的
· 资料库 → 各域文件/块数
· 待办 → 未完成事项与定时唤醒
· 技能 / 故障 / 模型 / 总览
· 参数 agent.max_rounds → 查某一项配置
"""


RULES = [
    ("人格面具", ("人格", "面具", "persona", "人格卡"), _personas),
    ("总览", ("总览", "概览", "现在的状态", "整体情况"), _overview),
    ("群/会话", ("群列表", "所有群", "有哪些群", "哪几个群", "群状态", "各群", "会话列表",
                "白名单", "激活状态"), _groups),
    ("记忆", ("记忆", "群印象", "印象", "人物档案", "群档案"), _memory),
    ("表情包", ("表情包", "表情库", "sticker"), _stickers),
    ("资料库", ("资料库", "知识库", "kb", "文档库"), _kb),
    ("待办", ("待办", "未完成", "唤醒", "待跟进"), _pending),
    ("技能", ("技能", "skill"), _skills),
    ("故障", ("故障", "报错", "错误计数", "计数器"), _faults),
    ("模型", ("模型", "provider", "用的什么"), _models),
    ("配置", ("参数", "配置"), lambda cfg, pdir, arg: _config_get(cfg, arg)),
]

_HELP_KWS = ("后台", "控制台", "能看什么", "有什么命令", "有哪些命令", "帮助")
# 命令动词：句首是这些词，才算"在使唤后台"（避免"这群人真多"这种正常聊天误触发）
_VERBS = ("列出", "列一下", "列下", "列举", "列个", "看看", "看一下", "看下", "查看", "查一下",
          "查查", "显示", "报一下", "报报", "说一下", "说下", "统计", "数一下", "列", "查")
_HEADS = ("你", "帮我", "麻烦", "请", "给我", "现在", "能不能", "可以", "顺便", "帮我看看")
# 只留"明确在问后台数据"的说法；「吗/呢/？」太宽（"我发你个表情包好不好笑？"会误触发）
_ASK_WORDS = ("有哪些", "有哪几个", "是哪几个", "是什么", "什么样", "多少", "几个", "情况",
              "列表", "清单", "都有啥", "都有什么", "是啥", "统计一下")


def _strip_heads(t: str) -> str:
    s = str(t or "")
    for _ in range(3):
        _hit = False
        for h in _HEADS:
            if s.startswith(h):
                s = s[len(h):].strip()
                _hit = True
                break
        if not _hit:
            break
    return s


def _is_cmd(t: str, kws) -> bool:
    """这句话像不像在使唤后台（而不是在闲聊）。要求：短 + 命中关键词 + 命令句式。"""
    t = str(t or "").strip()
    if not t or len(t) > 40:
        return False
    if not any(k in t for k in kws):
        return False
    s2 = _strip_heads(t)
    if any(s2.startswith(v) for v in _VERBS):
        return True
    if any(t.startswith(k) for k in kws if len(str(k)) >= 2):
        return True          # 「记忆 869622030」「参数 agent.max_rounds」这种开头就是关键词
    if any(w in t for w in _ASK_WORDS):
        return True
    cov = max((len(k) for k in kws if k in t), default=0) / float(max(1, len(t)))
    return cov >= 0.5


def route(text: str, cfg: dict = None, plugin_dir: str = "") -> tuple:
    """主人私聊里的一句话 → (是否后台命令, 回复片段)。纯读、纯函数。"""
    t = " ".join(str(text or "").split())
    if not t or len(t) > 40:
        return False, []
    pdir = _plugins_dir(plugin_dir)
    cfg = cfg or {}
    if _is_cmd(t, _HELP_KWS):
        return True, _pack("帮助", HELP.splitlines())
    for title, kws, fn in RULES:
        if not _is_cmd(t, kws):
            continue
        try:
            parts = fn(cfg, pdir, t)
        except Exception as e:
            return True, _pack("出错了", ["%s 读取失败：%r" % (title, e)])
        if parts:
            return True, parts
        return False, []
    return False, []
