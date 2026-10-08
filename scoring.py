"""零 token 的"该不该接话"打分器（纯本地启发式，不调用任何模型）。

设计思路
--------
1. 只做**概率调节**，不做硬放行/硬拦截；硬闸门（冷却、每小时上限、@/叫名字）在 main.py。
2. 所有信号都来自本地已有数据：当前消息文本、最近群聊摘要、消息时间戳、她自己说过的话。
3. 核心区分：「**正在跟我聊**」和「**群友自己聊得火热**」不是一回事，前者加分远大于后者。
4. 半价时段（DeepSeek 空闲价）稍微多说点；额度快用完时自动收着。
5. 每次决策都留下可读理由，方便事后调参。

最终概率 = clamp(base + Σ信号, p_min, p_max)，再抽签。
"""

import os
import random
import re
import time
from datetime import time as dtime

DEFAULT_CFG = {
    "enabled": True,
    "base": 0.08,               # 基础概率（什么信号都没有）
    "p_min": 0.03,
    "p_max": 0.40,              # 上限：再值得回也不会超过这个数

    # —— 硬档：明确在跟她说话（不走加法，直接给高概率）——
    "p_quote_me": 0.95,         # 别人"引用她的消息"说话 → 基本必回
    "p_continue": 0.90,         # 她刚说完，同一个人接着她的话说 / 内容明显在接她 → 基本必回
    "p_window": 0.40,
    "echo_strong": 0.34,        # 和她上一句的重合度到这个数才算"在接她的话说"           # 她刚说完，群里别人在这段时间里说话（弱一档，但有底）
    "p_hard_max": 0.98,

    # —— 加分：跟她的互动 ——
    "cont_after_bot": 0.25,     # 她刚说完话，同一个人的下一句（真·对话回合）
    "cont_after_bot_other": 0.12,   # 别人接在她后面（弱一些）
    "cont_window_sec": 90,
    "bot_echo": 0.12,           # 对方在复述/接她的梗

    # —— 加分：群聊氛围 ——
    "burst_mid": 0.03,          # 近 2 分钟 >=3 条
    "burst_high": 0.03,         # 近 2 分钟 >=6 条
    "burst_vhigh": 0.0,
    "burst_window_sec": 120,

    # —— 加分：内容 ——
    "question": 0.06,           # 是问句
    "meme_hit": 0.05,           # 命中群黑话/梗库
    "length_ok": 0.04,          # 5~40 字，有内容可接

    # —— 减分 ——
    "night": -0.05,             # 深夜
    "night_range": "00:30-06:30",
    "flood": -0.12,             # 同一个人刷屏（认定见下两行）
    "flood_fast_sec": 20,       # 20 秒内 >=5 条才算"快速连发"
    "flood_fast_count": 5,
    "flood_slow_sec": 60,       # 或 60 秒内 >=10 条才算"轰炸"
    "flood_slow_count": 10,
    "flood_scale": 0.50,        # 刷屏：整体打折
    "flood_count_hard": 15,      # 60 秒内 >=8 条：再打折
    "flood_scale_hard": 0.25,
    "tiny": -0.10,              # <=2 字 / 纯"哈哈""6"
    "mention_other": -0.25,     # 在 @ 别人
    "dup": -0.10,               # 和自己上一条几乎一样
    "just_replied": -0.12,      # 她 60s 内刚回过
    "just_replied_sec": 60,

    # —— 乘数 ——
    "offpeak_scale": 1.05,      # 半价时段
    "low_budget_scale": 0.30,   # 本小时额度用了 80%+
    "low_budget_ratio": 0.80,

    "log_decisions": True,
}

_QUESTION = re.compile(r"[?？]|吗|呢|怎么|为什么|为啥|啥|什么|哪|谁|是不是|能不能|可不可以|有没有|多少|几点")
_LAUGH = re.compile(r"^(?:[哈呵嘿嘻]{2,}|233+|草+|6+|乐+|典+|笑死|绷不住|难绷|啊+|哦+|嗯+|额+|。+|\.{3,}|[?？!！。，,、\s]+)$")
_TERM_SPLIT = re.compile(r"[／/|、,，]")


def _ngrams(text: str, n: int = 2) -> set:
    s = re.sub(r"\s+", "", text or "")
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def cont_pass(now, last_reply_ts, uid, to_uid, quotes_me, echo_hit,
              gap: float = 8.0, window: float = 180.0) -> bool:
    """是不是"对话延续"：她刚回过话，同一个人接着说 / 引用她 / 内容在接她的话。

    - gap：避免她刚说完 1 秒又接一句（连着刷）
    - window：超过这个时间就不算同一段对话了
    """
    try:
        if not last_reply_ts or not uid:
            return False
        dt = float(now) - float(last_reply_ts)
        if dt < float(gap or 0) or dt > float(window or 180):
            return False
        if to_uid and str(to_uid) == str(uid):
            return True                     # 正是她刚回的那个人接着说
        return bool(quotes_me or echo_hit)  # 引用她 / 内容接她的话
    except Exception:
        return False


def _overlap(a: str, b: str) -> float:
    """两条消息的字符 2-gram 重合度（0~1）。中文不分词也能用。"""
    A, B = _ngrams(a), _ngrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / float(min(len(A), len(B)))


def _parse_range(s: str, default) -> tuple:
    try:
        a, b = str(s).split("-")
        sh, sm = (int(x) for x in a.split(":"))
        eh, em = (int(x) for x in b.split(":"))
        return dtime(sh, sm), dtime(eh, em)
    except Exception:
        return default


class Scorer:
    def __init__(self, plugin_dir: str, cfg: dict | None = None):
        self.dir = plugin_dir
        self.cfg = cfg or {}
        self._terms: list = []
        self._terms_ts = 0.0
        self.last_detail: dict = {}

    # ---------------- 配置 / 词表 ----------------
    def opt(self, key: str):
        v = (self.cfg.get("scoring") or {}).get(key, None)
        if v is None:
            v = DEFAULT_CFG.get(key)
        return v

    def enabled(self) -> bool:
        try:
            return bool(self.opt("enabled"))
        except Exception:
            return True

    def terms(self) -> list:
        """从 memes.md 抽梗/黑话词条（10 分钟缓存）。"""
        now = time.time()
        if self._terms and now - self._terms_ts < 600:
            return self._terms
        path = os.path.join(self.dir, str(self.cfg.get("memes_file") or "memes.md"))
        out = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip().lstrip("-").strip()
                    if not line or line.startswith("【") or line.startswith("<!--"):
                        continue
                    head = line.split("：")[0].split(":")[0]
                    for t in _TERM_SPLIT.split(head):
                        t = t.strip()
                        if 2 <= len(t) <= 8 and not t.startswith("http"):
                            out.append(t)
        except Exception:
            out = []
        self._terms = out[:120]
        self._terms_ts = now
        return self._terms

    # ---------------- 工具：刷屏判定 ----------------
    def flooding(self, sender_times, sender_id: str, now: float = None) -> tuple:
        """返回 (是否刷屏, 20s 内条数, 60s 内条数)。刷屏 = 20s 内>=3 条 或 60s 内>=6 条。"""
        now = now or time.time()
        def _n(window, default):
            w = float(self.opt(window) or default)
            return len([1 for ts, uid in (sender_times or [])
                        if now - ts <= w and str(uid) == str(sender_id)])
        fast = _n("flood_fast_sec", 20)
        slow = _n("flood_slow_sec", 60)
        return (fast >= int(self.opt("flood_fast_count") or 3)
                or slow >= int(self.opt("flood_slow_count") or 6)), fast, slow

    # ---------------- 主入口 ----------------
    def probability(self, *, text: str, recent, msg_times, last_reply_ts: float = 0.0,
                    last_reply_to: str = "", sender_id: str = "", sender_times=None,
                    mentions_other: bool = False, sender_last_text: str = "",
                    offpeak: bool = True, now: float = None, quotes_me: bool = False) -> tuple:
        """返回 (概率, 理由列表)。任何异常都退化成 base（保守）。"""
        now = now or time.time()
        try:
            return self._probability(text=text, recent=recent, msg_times=msg_times,
                                     last_reply_ts=last_reply_ts, last_reply_to=last_reply_to,
                                     sender_id=sender_id, sender_times=sender_times,
                                     mentions_other=mentions_other, sender_last_text=sender_last_text,
                                     offpeak=offpeak, now=now, quotes_me=quotes_me)
        except Exception as e:
            return float(self.opt("base") or 0.1), ["打分器异常:%r" % (e,)]

    def _probability(self, *, text, recent, msg_times, last_reply_ts, last_reply_to, sender_id,
                     sender_times, mentions_other, sender_last_text, offpeak, now, quotes_me):
        t = (text or "").strip()
        p = float(self.opt("base"))
        why = []
        recent = list(recent or [])

        def add(k, label, scale=1.0):
            nonlocal p
            v = float(self.opt(k) or 0.0) * scale
            p += v
            why.append("%s%s%.2f" % (label, "+" if v >= 0 else "", v))

        def _mine(window, default):
            w = float(self.opt(window) or default)
            return len([1 for ts, uid in (sender_times or [])
                        if now - ts <= w and str(uid) == str(sender_id)])

        fast = _mine("flood_fast_sec", 20)
        slow = _mine("flood_slow_sec", 60)
        flooding = (fast >= int(self.opt("flood_fast_count") or 5)
                    or slow >= int(self.opt("flood_slow_count") or 10))
        # 正在跟她对话的人连发几条 = 正常聊天，不算自言自语（否则会误伤连续对话）
        _w0 = float(self.opt("cont_window_sec") or 180)
        if flooding and last_reply_ts and 0 <= now - last_reply_ts <= _w0 \
                and str(last_reply_to or "") == str(sender_id):
            flooding = False

        # 1) 先定"档位"：这句话到底是不是在跟她说话
        #    quote   = 别人引用着她的消息说话 / continue = 明显接着她的话 / window = 她刚说完后的余温
        tier = "normal"
        win = float(self.opt("cont_window_sec") or 180)
        in_win = bool(last_reply_ts) and 0 <= now - last_reply_ts <= win
        same = bool(last_reply_to) and str(last_reply_to) == str(sender_id)
        bot_lines = [x[1] for x in recent if str(x[0]) == "我"]   # 兼容 2/3 元组
        ov = _overlap(t, bot_lines[-1]) if (bot_lines and in_win) else 0.0
        quiet = (not flooding) and (not mentions_other)   # 刷屏的人和 @ 别人的不算在跟她说话
        if quiet and quotes_me:
            tier = "quote"
        elif quiet and in_win and (same or ov >= float(self.opt("echo_strong") or 0.34)):
            tier = "continue"
        elif quiet and in_win:
            tier = "window"

        # 2) 在接她的梗（软路径的加分，只有时间窗口内才算）
        if bot_lines and in_win and not flooding:
            if 0.15 <= ov < float(self.opt("echo_strong") or 0.34):
                half = float(self.opt("bot_echo") or 0) * 0.5
                p += half
                why.append("接梗(弱)+%.2f" % half)

        # 3) 群里聊得起劲（只是氛围，权重刻意比"跟她说话"低很多）
        bw = float(self.opt("burst_window_sec") or 120)
        burst = len([x for x in (msg_times or []) if now - x <= bw])
        if burst >= 10:
            add("burst_vhigh", "很热闹")
        if burst >= 6:
            add("burst_high", "热闹")
        if burst >= 3:
            add("burst_mid", "有人聊")

        # 4) 内容信号
        if _QUESTION.search(t):
            add("question", "问句")
        terms = self.terms()
        if terms and any(x in t for x in terms):
            add("meme_hit", "命中梗")
        if 5 <= len(t) <= 40:
            add("length_ok", "有内容")
        if len(t) <= 2 or _LAUGH.match(t):
            add("tiny", "太短/纯语气")

        # 5) 刷屏 / 重复 / 在 @ 别人
        if flooding:
            add("flood", "自言自语")
        if t and sender_last_text and _overlap(t, sender_last_text) >= 0.85:
            add("dup", "复读")
        if mentions_other:
            add("mention_other", "在@别人")

        # 6) 深夜
        s, e = _parse_range(self.opt("night_range"), (dtime(0, 30), dtime(6, 30)))
        lt = time.localtime(now)
        if s <= dtime(lt.tm_hour, lt.tm_min) < e:
            add("night", "深夜")

        # 7) 她刚回过 → 收着点
        if last_reply_ts and 0 <= now - last_reply_ts <= float(self.opt("just_replied_sec") or 60):
            add("just_replied", "刚回过")

        # 8) 乘数：刷屏打大折 / 半价时段 / 额度快满
        if flooding:
            sc = float(self.opt("flood_scale") or 0.5)
            if slow >= int(self.opt("flood_count_hard") or 6):
                sc *= float(self.opt("flood_scale_hard") or 0.25)
            p *= sc
            why.append("刷屏x%.2f" % sc)
        if offpeak:
            sc = float(self.opt("offpeak_scale") or 1.0)
            if sc != 1.0:
                p *= sc
                why.append("半价时段x%.2f" % sc)
        try:
            ratio = float(self.cfg.get("_hour_ratio", 0.0))
        except Exception:
            ratio = 0.0
        if ratio >= float(self.opt("low_budget_ratio") or 0.8):
            sc = float(self.opt("low_budget_scale") or 0.3)
            p *= sc
            why.append("额度将满x%.2f" % sc)

        lo = float(self.opt("p_min") or 0.0)
        p = max(lo, min(float(self.opt("p_max") or 1.0), p))     # 软路径先按常规上限压
        if tier == "quote":
            p = float(self.opt("p_quote_me") or 0.95)
            why.insert(0, "★引用她的消息→必回")
        elif tier == "continue":
            p = float(self.opt("p_continue") or 0.90)
            why.insert(0, "★接着她的话说→必回")
        elif tier == "window":
            p = max(p, float(self.opt("p_window") or 0.4))
            why.insert(0, "她刚说过话(%.0fs内)" % (now - last_reply_ts))
        # 额度快满时所有档位一起收着（硬档也受每小时上限约束）
        try:
            ratio = float(self.cfg.get("_hour_ratio", 0.0))
        except Exception:
            ratio = 0.0
        if ratio >= float(self.opt("low_budget_ratio") or 0.8):
            sc = float(self.opt("low_budget_scale") or 0.3)
            p *= sc
            why.append("额度将满x%.2f" % sc)
        p = max(lo, min(float(self.opt("p_hard_max") or 0.98), p))
        self.last_detail = {"p": round(p, 3), "why": why, "burst": burst, "tier": tier,
                            "flooding": flooding, "fast": fast, "slow": slow}
        return p, why


def roll(p: float) -> bool:
    return random.random() < float(p)
