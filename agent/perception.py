# -*- coding: utf-8 -*-
"""环境感知：把"现在几点、群里什么气氛、我自己多久没说话了"算成一小段提示。

- 全部是**零 token** 的本地启发式：只看当前的聊天记录（`(who, text, uid, ts)` 元组），
  不调模型、不联网、不落盘。
- 目的：让她对"现实世界"有点感知——说话的时候知道是几点、群里是热闹还是冷清、
  是在斗图还是在吵架、自己刚说过还是很久没冒头，别再拿旧话题当现在的事。
- 注入位置：用户消息末尾（时间锚点旁边），只作参考，不让她念出来。
"""
import json
import os
import re
import time

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_WEEK = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 氛围关键词（粗粒度够用了，不当情绪分析）
_HOT = ("傻逼", "沙碧", "煞笔", "傻b", "滚", "草泥马", "尼玛", "妈的", "tmd", "TMD",
        "你妈", "急了", "闭嘴", "别吵", "吵死", "骂", "晦气", "垃圾", "逆天")
_FUN = ("哈哈", "笑死", "难绷", "绷不住", "乐", "666", "233", "😂", "🤣", "😆", "笑")
_IMG_RE = re.compile(r"^\s*[\[<]图片")


def _ts(it) -> float:
    try:
        return float(it[3]) if len(it) > 3 and it[3] else 0.0
    except Exception:
        return 0.0


def _ago(sec: float) -> str:
    """把秒数说成人话。"""
    try:
        sec = float(sec)
    except Exception:
        return "?"
    if sec < 60:
        return "%d 秒" % int(sec)
    if sec < 3600:
        return "%d 分钟" % int(sec // 60)
    if sec < 86400:
        return "%.1f 小时" % (sec / 3600.0)
    return "%.1f 天" % (sec / 86400.0)


def _daypart(hour: int) -> str:
    if hour < 5:
        return "凌晨"
    if hour < 9:
        return "早上"
    if hour < 12:
        return "上午"
    if hour < 14:
        return "中午"
    if hour < 18:
        return "下午"
    if hour < 20:
        return "傍晚"
    if hour < 24:
        return "晚上"
    return "深夜"


_HOL_CACHE = {"key": None, "off": {}, "work": {}}


def _holidays(cfg: dict = None) -> tuple:
    """读 holidays.json（按 mtime 缓存）→ ({日期: 假期名}, {日期: 调休名})。"""
    path = os.path.join(PLUGIN_DIR, str((cfg or {}).get("holidays_file") or "holidays.json"))
    try:
        key = os.path.getmtime(path)
    except Exception:
        key = None
    if key is not None and _HOL_CACHE["key"] == key:
        return _HOL_CACHE["off"], _HOL_CACHE["work"]
    off, work = {}, {}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f) or {}
        off = {str(k): str(v) for k, v in (d.get("offDays") or {}).items()}
        work = {str(k): str(v) for k, v in (d.get("workDays") or {}).items()}
    except Exception:
        pass
    _HOL_CACHE.update({"key": key, "off": off, "work": work})
    return off, work


def date_sense(cfg: dict = None, now=None) -> dict:
    """日期感：今天几号星期几、是不是假期/周末/调休、明天上不上班、离下个假期多久。"""
    now = float(now or time.time())
    t = time.localtime(now)
    today = time.strftime("%Y-%m-%d", t)
    off, work = _holidays(cfg)
    sig = {"date": today, "md": time.strftime("%m-%d", t), "weekday": _WEEK[t.tm_wday],
           "weekend": t.tm_wday >= 5, "holiday": off.get(today, ""),
           "makeup": work.get(today, ""), "day_no": 0, "day_total": 0,
           "last_day": False, "tomorrow": "", "next_name": "", "next_days": 0}

    def _is_off(d: str) -> bool:
        return d in off

    def _is_work_makeup(d: str) -> bool:
        return d in work

    # 今天在假期里的第几天 / 这个假期一共几天
    if sig["holiday"]:
        _d0 = t
        _n = 0
        while True:                      # 往前数
            _n += 1
            _prev = time.strftime("%Y-%m-%d", time.localtime(now - 86400 * _n))
            if not _is_off(_prev):
                break
        sig["day_no"] = _n
        _k, _tot = 1, _n                 # 从明天开始往后数
        while True:
            _nt = time.strftime("%Y-%m-%d", time.localtime(now + 86400 * _k))
            if not _is_off(_nt):
                break
            _tot += 1
            _k += 1
        sig["day_total"] = _tot
        sig["last_day"] = (_n == _tot)
    # 明天
    _tm = time.strftime("%Y-%m-%d", time.localtime(now + 86400))
    _tm_wd = (t.tm_wday + 1) % 7
    if _is_off(_tm):
        sig["tomorrow"] = "明天还放假（%s）" % off.get(_tm, "假")
    elif _is_work_makeup(_tm):
        sig["tomorrow"] = "明天是调休上班日（%s）" % work.get(_tm, "调休")
    elif _tm_wd >= 5:
        sig["tomorrow"] = "明天是周末"
    else:
        sig["tomorrow"] = "明天正常上班/上学"
    # 下一个假期（往后找 200 天；文件里没有就是还没公布，先不说）
    for _i in range(1, 201):
        _d = time.strftime("%Y-%m-%d", time.localtime(now + 86400 * _i))
        if _is_off(_d) and not _is_off(time.strftime("%Y-%m-%d",
                                                    time.localtime(now + 86400 * (_i - 1)))):
            sig["next_name"] = off.get(_d, "假期")
            sig["next_days"] = _i
            break
    return sig


def render_date(dsig: dict) -> str:
    """日期那一行。"""
    if not dsig:
        return ""
    today = str(dsig.get("date") or "")
    bits = ["%s %s" % (dsig.get("md") or today, dsig.get("weekday") or "")]
    if dsig.get("holiday"):
        _d, _t = int(dsig.get("day_no") or 0), int(dsig.get("day_total") or 0)
        if _t > 1:
            bits.append("**%s假期第 %d 天（共 %d 天）**%s" % (
                dsig["holiday"], _d, _t, "，是最后一天" if dsig.get("last_day") else ""))
        else:
            bits.append("今天是 %s" % dsig["holiday"])
    elif dsig.get("makeup"):
        bits.append("**今天是调休上班日**（%s调休，虽然是%s）" % (dsig["makeup"],
                                                     dsig.get("weekday") or "周末"))
    elif dsig.get("weekend"):
        bits.append("周末休息日")
    else:
        bits.append("普通工作日")
    if dsig.get("tomorrow"):
        bits.append(str(dsig["tomorrow"]))
    if not dsig.get("holiday") and dsig.get("next_name"):
        _n = int(dsig.get("next_days") or 0)
        bits.append("离下一个假期（%s）还有 %d 天" % (dsig["next_name"], _n))
    return "日期：" + "；".join([b for b in bits if b])


def _words(t: str) -> set:
    """粗分词：中文按 2 字滑窗取片段，用来测"话题有没有换"。"""
    s = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]+", " ", str(t or ""))
    out = set()
    for w in s.split():
        if len(w) <= 2:
            out.add(w)
        else:
            for i in range(len(w) - 1):
                out.add(w[i:i + 2])
    return out


def sense(recent, cfg: dict = None, now=None, my_name: str = "我",
          last_reply_ts: float = 0.0, hour_used: int = 0, hour_cap: int = 0,
          at_me: bool = False, called: bool = False) -> dict:
    """算环境信号（纯函数，不碰任何外部状态）。"""
    now = float(now or time.time())
    rows = []
    for it in list(recent or [])[-40:]:
        try:
            rows.append((_ts(it), str(it[0] or ""), str(it[1] or "")))
        except Exception:
            continue
    rows = [r for r in rows if r[2] or r[1] == "<图片>"]
    sig = {
        "date": date_sense(cfg, now),
        "empty": not rows,
        "now": now,
        "at_me": bool(at_me or called),
        "n10": 0, "speakers10": 0, "last_age": 0.0, "gap": 0.0,
        "my_last_age": 0.0, "since_me": 0, "hour_used": int(hour_used or 0),
        "hour_cap": int(hour_cap or 0), "hot": 0, "fun": 0, "imgs": 0, "unseen_imgs": 0,
        "topic_shift": False, "burst": False,
    }
    if not rows:
        return sig
    t_last = rows[-1][0] or now
    sig["last_age"] = max(0.0, now - t_last)
    if len(rows) >= 2:
        t_prev = rows[-2][0] or t_last
        sig["gap"] = max(0.0, t_last - t_prev)
    last10 = [r for r in rows if r[0] and (now - r[0]) <= 600]
    sig["n10"] = len(last10)
    sig["speakers10"] = len({r[1] for r in last10
                               if r[1] not in (my_name, "<图片>")})
    sig["burst"] = len(last10) >= 15
    sig["imgs"] = sum(1 for r in last10
                    if r[1] == "<图片>" or _IMG_RE.match(r[2]))
    # 图消息占位（who == "<图片>" 且没有描述）= 她没看过内容的那几张
    sig["unseen_imgs"] = sum(1 for r in last10
                             if r[1] == "<图片>" and not str(r[2] or "").strip())
    _blob = " ".join(r[2] for r in last10)
    sig["hot"] = sum(1 for w in _HOT if w in _blob)
    sig["fun"] = sum(1 for w in _FUN if w in _blob)
    mine = [i for i, r in enumerate(rows) if r[1] == my_name]
    if mine:
        _mi = mine[-1]
        sig["my_last_age"] = max(0.0, now - (rows[_mi][0] or now))
        sig["since_me"] = len(rows) - 1 - _mi
    elif last_reply_ts:
        sig["my_last_age"] = max(0.0, now - float(last_reply_ts))
    # 话题是否刚换过：最后 3 条 vs 之前 5 条的重合度
    try:
        tail_w = set().union(*[_words(r[2]) for r in rows[-3:]])
        prev_w = set().union(*[_words(r[2]) for r in rows[-8:-3]])
        if tail_w and prev_w:
            overlap = len(tail_w & prev_w) / float(max(1, len(tail_w)))
            sig["topic_shift"] = overlap < 0.15
    except Exception:
        pass
    return sig


def render(sig: dict, cfg: dict = None, private: bool = False) -> str:
    """把信号渲染成一小段提示文本（约 3 行，≤ max_chars）。"""
    if not sig:
        return ""
    cap = 420
    try:
        pcfg = (cfg or {}).get("perception") or {}
        if pcfg.get("enabled", True) is False:
            return ""
        cap = int(pcfg.get("max_chars", 420) or 420)
    except Exception:
        pass
    now = float(sig.get("now") or time.time())
    lt = time.localtime(now)
    _place = "对话里" if private else "群里"
    _other = "对方" if private else "群里"
    lines = ["【当下感知（系统算的实时信号，只作参考，别念出来）】"]
    # ① 时间感
    _t1 = "现在是 %d-%02d-%02d %s %02d:%02d（%s）" % (
        lt.tm_year, lt.tm_mon, lt.tm_mday, _WEEK[lt.tm_wday], lt.tm_hour, lt.tm_min,
        _daypart(lt.tm_hour))
    if sig.get("last_age"):
        _t1 += "；最新这条是 %s前发的" % _ago(sig["last_age"])
    if sig.get("gap") and sig["gap"] >= 600:
        _t1 += "；**%s刚才安静了 %s**，别把更早的话当成现在的话题" % (_place,
                                                          _ago(sig["gap"]))
    if sig.get("last_age") and sig["last_age"] >= 600:
        _t1 += "；这已经是十几分钟前的消息了"
    lines.append("- " + _t1)
    # ①b 日期 / 节假日
    _dl = render_date(sig.get("date") or {})
    if _dl:
        lines.append("- " + _dl)
    # ② 气氛
    _n, _s = int(sig.get("n10") or 0), int(sig.get("speakers10") or 0)
    if sig.get("burst"):
        _mood = "刷屏中"
    elif _n >= 8:
        _mood = "挺热闹"
    elif _n >= 3:
        _mood = "正常节奏"
    else:
        _mood = "比较冷清"
    _t2 = "气氛：最近 10 分钟 %d 条、%d 个%s在说话（%s）" % (
        _n, _s, ("人" if not private else "人"), _mood)
    if int(sig.get("hot") or 0) >= 2 and int(sig.get("hot") or 0) > int(sig.get("fun") or 0):
        _t2 += "；有人上火/在骂，别凑热闹、也别跟着飙脏话"
    elif int(sig.get("fun") or 0) >= 2:
        _t2 += "；在乐/在开玩笑，可以跟着闹"
    if int(sig.get("imgs") or 0) >= 2:
        _t2 += "；在发图/斗图"
    if int(sig.get("unseen_imgs") or 0) > 0:
        _t2 += ("；**最近有 %d 张图你没看到内容**——别猜图里是什么，也别装看过"
                % int(sig["unseen_imgs"]))
    if sig.get("topic_shift"):
        _t2 += "；**话题刚换过**"
    lines.append("- " + _t2)
    # ③ 她自己
    if sig.get("empty"):
        out0 = "\n".join(lines)
        return out0[:cap] if len(out0) > cap else out0
    _t3 = ""
    if sig.get("my_last_age"):
        _t3 = "你上次说话是 %s前" % _ago(sig["my_last_age"])
        if int(sig.get("since_me") or 0) > 0:
            _t3 += "（之后%s过了 %d 条）" % (_other, int(sig["since_me"]))
    else:
        _t3 = "%s你还没说过话" % _place
    if sig.get("hour_cap"):
        _t3 += "；这一小时你已经说了 %d/%d 条" % (int(sig.get("hour_used") or 0),
                                                 int(sig["hour_cap"]))
    if sig.get("at_me"):
        _t3 += "；**这条是在叫你/回你**，正常接就行"
    lines.append("- " + _t3)
    out = "\n".join(lines)
    if len(out) > cap:
        out = out[: cap - 1] + "…"
    return out
