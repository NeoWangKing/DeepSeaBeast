"""高峰/低峰触发门控 + 零 token 回复决策打分。

- 高峰时段（工作日 9-12/14-18，非法定节假日，DeepSeek 标准价）：只有被 @ 或叫名字才回复（斜杠指令放行）。
- 空闲时段（DeepSeek 半价）：被 @ / 被引用照常回复；普通消息交给 scoring.py 打分决定概率。
- 打分只做概率调节，硬闸门（冷却、每小时上限、额度乘数）仍在本地计算，不消耗任何 token。
"""
import asyncio
import base64
import json
from collections import deque
import os
import random
import urllib.request
import re
import time
from datetime import datetime, time as dtime

from astrbot.api.event import AstrMessageEvent, filter, MessageChain
from astrbot.api.message_components import At, Image, Plain, Reply
try:
    from astrbot.api.message_components import Face          # QQ 自带表情（小黄脸）
except Exception:
    Face = None
try:
    from astrbot.api.message_components import Poke          # 拍一拍/戳一戳
except Exception:
    try:
        from astrbot.core.message.components import Poke      # 老版本路径
    except Exception:
        Poke = None
from astrbot.api.star import Context, Star, register

try:
    from . import scoring, stickers, kb as local_kb, followup, replyproto, promptlib
    from . import agent   # AstrBot 以包形式加载插件
    from .memory import store as mem_store     # 记忆产物只读访问
    from .games.turtle_soup import judge as turtle_judge, puzzles as turtle_puzzles, session as turtle_session
except Exception:                              # 兜底：直接当脚本/被 py_compile 时
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    import scoring
    import stickers
    import kb as local_kb
    import followup
    import replyproto
    import promptlib
    import agent
    from memory import store as mem_store
    from games.turtle_soup import judge as turtle_judge, puzzles as turtle_puzzles, session as turtle_session
    from games.turtle_soup import gen as turtle_gen

_TURTLE_REACT = __import__("re").compile(
    r"^(?:[哈呵嘿嘻]{2,}|233+|草+|6+|乐+|典+|笑死|绷不住了?|难绷|啊+|哦+|嗯+|额+|好+|牛[逼批]|nice|"
    r"\.{2,}|[?？!！。，,、~～\s]+)$", __import__("re").I)

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", "system_prompt.txt")

DEFAULTS = {
    "offpeak": "00:30-08:30",
    "auto_reply_probability": 0.25,
    "keywords": ["小鲸鱼", "大肥鱼", "肥鱼"],
    "min_interval_sec": 180,
    "max_auto_per_hour": 6,
    "force_mode": "",
    "inject_memes": True,
    "memes_file": "memes.md",
    "image_fastpath": False,         # 默认关：当前模型(deepseek-flash)能识图，不该摘。只有换到纯文本模型时才开
    "smart_quote": True,
    "engaged_interval_sec": 6,
    "engaged_window_sec": 90,
    "engaged_rest_sec": 60,
    "max_engaged_streak": 6,   # 连击上限：设得比每小时上限大，实际由"每小时 N 条"兜底
    "quote_when_stale": True,
    "quote_when_replied": False,
    "turtle": {                      # 海龟汤：全程走 GLM 判题，不消耗 DeepSeek
        "enabled": False,            # 默认关：必须在 config.json 里显式开启
        "groups": [],                # 空 = 不限制；填群号则只在那些群能开
        "question_filter": "llm",    # 怎么判断"这条算不算提问"：
                                     #   llm      = 本地预筛 + GLM 自己判断（推荐，闲聊不吭声）
                                     #   at       = 严格：只有真正 @她 才算（开局同此要求）
                                     #   at_quote = @她 或 引用她 才算
                                     #   all      = 每条都当提问
        "at_only": False,            # 兼容旧配置：等同于 question_filter="at"
        "start_needs_at": None,      # 开局是否必须 @她：None=自动（严格模式要，其他群不用）
        "loose_start_keywords": ["海龟汤", "海龟"],   # 不用 @ 时，光凭这些词也能开局
        "start_keywords": ["海龟汤", "开局"],
        "exit_keywords": ["揭晓", "结束", "不玩了", "退出", "收工"],
        "change_keywords": ["换一个", "换一道", "换题", "换个新的", "再来一个", "不玩这个"],   # 局中换题
        "surface_keywords": ["汤面", "谜面", "题面"],        # 局中想再看一眼题目
        "stale_sec": 1500,           # 一局超过这么久没人问，就当弃局，下次说「开局」重新出题
        "reuse_cooldown_days": 365,  # 同一个群出过的题，多少天内不再重出
        "use_web": True,             # 抽题时要不要带上「网上题库导入」的那批（import_web.py 生成）
        "ai_gate": True,             # 开局/换题前先让 AI 判一句"这条是不是真的要开局"（防关键词误触发）
        "progress_every": 3,         # 每问几条就评估一次"玩家猜到哪了"
        "difficulty": "hard",        # 难度：hard=基本不给提示；easy=每次可给一句补充
        "hint_after_dry": 3,         # 困难档：连着问出 3 个「不重要」就给一次简短提示
        "hard_hint_chars": 16,       # 困难档提示字数（只给方向，不许出现汤底关键名词）
        "easy_hint_chars": 25,       # 简单档补充字数
        "easy_words": ["简单", "新手", "轻松", "休闲", "别太难"],     # 群里可以点难度
        "hard_words": ["困难", "硬核", "地狱", "挑战"],
        "hint_ask_words": ["提示", "线索", "给点思路", "没思路", "想不出来", "卡住", "给点方向"],
        "kb": {                      # 本地小资料库（data/kb/ 里的 md/txt）
            "enabled": True,
            "top_k": 3,              # 最多注入几块
            "min_score": 0.14,       # 相关度门槛（低了容易污染上下文）
            "max_chars": 900,        # 最多注入多少字
        },
        "search": {                  # 上网搜索 / 读网页（求证用）
            "enabled": True,
            "provider": "auto",      # auto=优先用 AstrBot 里配了 key 的 provider，没 key 走 Firecrawl 免 key
            "count": 5,              # 默认返回几条
            "max_per_hour": 20,      # 每小时最多搜几次（防刷/防额度）
            "read_max_chars": 4000,  # 单个网页最多读多少字给她
            "cache_ttl": 600,        # 搜索结果缓存（秒）
            "read_cache_ttl": 3600,  # 网页正文缓存（秒）
            "timeout": 20,
        },
        "stickers": {                # 表情包收藏夹
            "enabled": True,
            "collect_exclude_groups": ["966812151"],   # 这些群不自动收图（隐私优先）
            "send_exclude_groups": ["966812151"],      # 这些群不发图
            "max_store": 300,        # 收藏上限（按"最少用+最久没用"淘汰）
            "max_per_hour": 3,       # 每小时最多发几张
            "collect_max_per_hour": 10,       # 群聊里每小时最多收几张
            "collect_private": True,          # 私聊发图也收（自己人发的多半是精选）
            "collect_prob": 0.7,              # 群聊里每张合格的图有多大概率被收下（不是全收）
            "private_collect_prob": 0.95,     # 私聊的收录概率（自己发的多半是精选，几乎都收）
            "private_max_per_hour": 50,       # 私聊每小时上限放宽
            "send_private": True,             # 私聊也能发表情包
            "vision_model": "glm-4v-flash",
            "collect_say_prob": 0.18,            # 收图后偶尔开口夸一句的概率（大部分时候什么都不说）
            "collect_say_max_per_hour": 2,       # 这种"顺手夸一句"每小时最多几次
            "collect_say_lines": ["好图", "这张有意思，偷了", "偷了", "笑死，存了", "这个我收下了"],
            "prompt_max": 12,             # 提示词里列几张给她挑
            "encourage": 2,               # 表情档 0~3（越大越爱发）
            "nudge_prob": 0.35,           # 闲聊里顺手提醒她可以配表情的概率（不强制）
        },
        "poke_reply": {              # 有人拍一拍/戳一戳她
            "enabled": True,
            "max_per_hour": 4,       # 每小时最多回几次（防刷）
            "min_interval_sec": 20,  # 两次回应至少隔这么久
            "lines": ["？", "干嘛", "别戳", "戳我干嘛", "？你礼貌吗", "再戳就把你吃掉"],
        },
        "progress_words": ["进度", "问到哪", "问过什么", "已经知道", "已知信息", "猜出什么", "总结一下",
                           "复盘", "目前知道", "整理一下线索", "我们玩到哪"],
        "asked_hint_chars": 20,      # 玩家主动讨提示时的字数上限（可以点到关键词，但不给结论）
        "gen": {                     # 现货用完时让 AI 现编一道（写进题库，所有群都能抽到）
            "on_demand": True,
            "tries": 4,              # 现编时最多试几次（一次 ≈ 2.6k token）
            "budget": 30000,         # 现编一次最多花多少 token
            "pregen_when_left": 1,   # 群里剩几道没出过的题时，就提前在后台补一道
            "max_per_day": 3,        # 现编/预生成每天最多几次（硬闸，防刷爆 token）
            "max_ai": 120,           # 后台预生成最多把 AI 题库囤到多少道
            "headsup": "题库里的题你们都刷过啦，我现编一道新的，稍等半分钟……",
        },
        "opening": "🐢 海龟汤开局！【{category}】\n\n【汤面】{surface}\n\n开始问吧——我只回「是 / 不是 / 不重要」。想结束就说「揭晓」。",
        "reveal": "【汤底】{bottom}\n\n—— 这局到此为止，想再来一局就说「海龟汤」。",
        "fact_every": 10,
        "answer_delay_ms": 1200,     # 答题前装一会儿人（毫秒，会带随机抖动）
        "quote": True,               # 回复时带上引用（能看清在答哪一条）
        "afterglow_sec": 1800,       # 一局揭晓后，这道题在她脑子里留多久（用于事后追问）
        "after_hint": "\n\n有没看明白的地方，@我 问就行。",
        "per_group": {},             # 每群覆盖，如 {"966812151": {"question_filter": "at", "no_disk": true}}
    },
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    forced = os.environ.get("QQ_PEAK_GATE_FORCE", "").strip()
    if forced:
        cfg["force_mode"] = forced
    return cfg


def _peak_windows(cfg: dict) -> list:
    out = []
    for w in cfg.get("peak_windows") or ["09:00-12:00", "14:00-18:00"]:
        try:
            a, b = str(w).split("-")
            sh, sm = (int(x) for x in a.split(":"))
            eh, em = (int(x) for x in b.split(":"))
            out.append((dtime(sh, sm), dtime(eh, em)))
        except Exception:
            continue
    return out


_HOLIDAY_CACHE = {"ts": 0.0, "days": set()}


def _holiday_days(cfg: dict) -> set:
    now = time.time()
    if _HOLIDAY_CACHE["days"] and now - _HOLIDAY_CACHE["ts"] < 3600:
        return _HOLIDAY_CACHE["days"]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        str(cfg.get("holidays_file") or "holidays.json"))
    days = set()
    try:
        with open(path, encoding="utf-8") as f:
            days = set((json.load(f).get("offDays") or {}).keys())
    except Exception:
        pass
    _HOLIDAY_CACHE["ts"] = now
    _HOLIDAY_CACHE["days"] = days
    return days


def in_offpeak(cfg: dict) -> bool:
    """DeepSeek 定价：周一至周五（非法定节假日）09:00-12:00、14:00-18:00 为高峰，其余为空闲。"""
    mode = str(cfg.get("force_mode") or "").strip().lower()
    if mode in ("peak", "offpeak"):
        return mode == "offpeak"
    now = datetime.now()
    if now.weekday() >= 5:
        return True                                  # 周末全天空闲
    if now.strftime("%Y-%m-%d") in _holiday_days(cfg):
        return True                                  # 法定节假日全天空闲
    t = now.time()
    for s, e in _peak_windows(cfg):
        if s <= t < e:
            return False                             # 高峰
    return True                                      # 午休/夜间等其余时段 = 空闲


@register("qq_peak_gate", "local", "高峰只允许@触发，低峰允许智能接话", "0.1.0")
def _safe_cut(t: str, pos: int) -> int:
    """把切分位置挪开英文单词/数字中间（否则会把 BigFish 切成 BigFa / sh）。"""
    n = len(t)
    pos = max(1, min(n - 1, int(pos)))

    def _w(ch):
        return ch.isalnum() and ord(ch) < 128

    if pos < n and _w(t[pos - 1]) and _w(t[pos]):
        i = pos
        while i < n and _w(t[i]):
            i += 1
        j = pos
        while j > 0 and _w(t[j - 1]):
            j -= 1
        return i if (i - pos) <= (pos - j) else j
    return pos


_PRIVATE_ET = (getattr(filter.EventMessageType, "FRIEND_MESSAGE", None)
               or getattr(filter.EventMessageType, "PRIVATE_MESSAGE", None)
               or filter.EventMessageType.GROUP_MESSAGE)


class QqPeakGate(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.cfg = load_config()
        self._agent_sessions: dict = {}      # agent loop / 工具会话（每次唤醒一个）
        self._agent_locks: dict = {}         # 每个会话一把锁：loop 串行化（连发时排队）
        self._img_cache: dict = {}           # 最近收到的图（message_id → 路径），供收藏用
        self.last_auto: dict[str, float] = {}
        self.hour_count: dict[tuple[str, int], int] = {}
        self._nick: str = ""
        self._nick_ts: float = 0.0
        self.recent: dict[str, deque] = {}
        self.msg_times: dict[str, deque] = {}
        self._memes: str = ""
        self._memes_ts: float = 0.0
        self.last_msg: dict[str, tuple] = {}
        self.last_reply_ts: dict[str, float] = {}    # 该群她最后一次开口的时间
        self.sender_times: dict[str, deque] = {}     # (时间, 发送者) 用来识别刷屏
        self.sender_text: dict[tuple, str] = {}      # (群, 人) -> 上一条内容，用来识别复读
        self.engaged_streak: dict[str, tuple] = {}   # (连续接话次数, 时间)
        self.last_speaker: dict[str, str] = {}       # 该群最后一条消息的发送者
        self.prev_speaker: dict[str, str] = {}       # 上一条的前一条（判断"连发"用） —— 防止她一个人连珠炮
        self.last_reply_to: dict[str, str] = {}      # 她上一次在跟谁说话
        self._t_in: dict[str, float] = {}            # 收消息时间，用来量各段耗时
        self._img_flag: dict[str, bool] = {}         # 本条消息里有图片（已摘掉）
        self._img_hint: dict[str, bool] = {}         # 需要在 prompt 里补图片提示
        try:
            self.scorer = scoring.Scorer(PLUGIN_DIR, self.cfg)
        except Exception as e:
            self.scorer = None
            self._log("scoring 初始化失败: %r" % (e,))

    def _log(self, msg: str) -> None:
        try:
            self.logger.info("[peak_gate] %s", msg)
        except Exception:
            print("[peak_gate]", msg, flush=True)

    def _log_hook(self, m):
        self._log(m)

    def _log_debug(self, msg: str) -> None:
        try:
            self.logger.debug("[peak_gate] %s", msg)
        except Exception:
            pass

    def _remember(self, gid: str, event: AstrMessageEvent, text: str) -> None:
        try:
            lines = int(self.cfg.get("context_lines", 6))
        except Exception:
            lines = 6
        if self._in("no_context_groups", gid):
            return                                # 该群不记上下文
        buf = self.recent.get(gid)
        if buf is None:
            buf = deque(maxlen=max(2, lines))
            self.recent[gid] = buf
        who = ""
        try:
            who = str(event.get_sender_name() or event.get_sender_id() or "")
        except Exception:
            who = "某人"
        t = " ".join((text or "").split())
        if len(t) > 60:
            t = t[:60] + "…"
        if t:
            try:
                uid_now = str(event.get_sender_id() or "")
            except Exception:
                uid_now = ""
            buf.append((who, t, uid_now))


    def _log_chat(self, event: AstrMessageEvent, text: str) -> None:
        """把群消息落盘（按天 jsonl），供 extract_memes.py 提炼本群黑话。"""
        try:
            if not self.cfg.get("log_chat", True):
                return
            gid = str(event.get_group_id() or "")
            text = " ".join((text or "").split())
            if not gid or gid == "0" or not text:
                return
            if self._in("no_log_groups", gid):
                return                                # 该群不留语料
            d = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             str(self.cfg.get("log_dir", "chatlog")))
            os.makedirs(d, exist_ok=True)
            fn = os.path.join(d, datetime.now().strftime("%Y-%m-%d") + ".jsonl")
            rec = {"t": int(time.time()), "g": gid, "u": str(event.get_sender_id() or ""),
                   "n": (event.get_sender_name() or "")[:20], "x": text[:200]}
            with open(fn, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            self._log("log_chat ERROR: %r" % (e,))

    def _reload_cfg(self) -> None:
        """热加载：config.json 一改就生效，不用重启 AstrBot。"""
        try:
            m = os.stat(CONFIG_PATH).st_mtime
            if m != getattr(self, "_cfg_mtime", 0):
                self._cfg_mtime = m
                self.cfg = load_config()
                if self.scorer is not None:
                    self.scorer.cfg = self.cfg
                self._log("配置已热加载: force=%r prob=%s" % (
                    self.cfg.get("force_mode"), self.cfg.get("auto_reply_probability")))
        except Exception:
            pass

    def _memes_text(self) -> str:
        """梗库：静态注入，命中 prompt 缓存后几乎不花钱。"""
        now = time.time()
        if self._memes and now - self._memes_ts < 600:
            return self._memes
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            str(self.cfg.get("memes_file") or "memes.md"))
        try:
            with open(path, encoding="utf-8") as f:
                self._memes = f.read().strip()
        except Exception:
            self._memes = ""
        self._memes_ts = now
        return self._memes

    def _in(self, key: str, gid: str) -> bool:
        try:
            return str(gid or "") in [str(x) for x in (self.cfg.get(key) or [])]
        except Exception:
            return False

    def _reply_delay(self, text: str, gid: str = "", uid: str = "") -> float:
        """拟人延迟：base + 长度/打字速度，带随机抖动。返回秒数。"""
        try:
            hd = self.cfg.get("human_delay") or {}
            if not hd.get("enabled", True):
                return 0.0
            base = float(hd.get("base_sec", 1.2))
            cps = max(1.0, float(hd.get("chars_per_sec", 12)))
            mx = float(hd.get("max_sec", 6.0))
            mn = float(hd.get("min_sec", 0.8))
            jit = float(hd.get("jitter", 0.2))
            short_chars = int(hd.get("short_chars", 3))
            short_wait = float(hd.get("short_wait_sec", 2.5))
            burst_wait = float(hd.get("burst_wait_sec", 3.5))
        except Exception:
            base, cps, mx, mn, jit = 1.2, 12.0, 6.0, 0.8, 0.2
            short_chars, short_wait, burst_wait = 3, 2.5, 3.5
        n = len(text or "")
        # 单字消息：很可能是"逐字发"的一条，先多等一会儿（不然会抢答第一个字）
        if n <= short_chars:
            d = max(base + n / cps, short_wait)
        else:
            d = base + n / cps
        # 他最近 12 秒内连着发了几条超短消息 → 明显在逐字打，等更久
        try:
            t0 = time.time()
            lens = [x for x in (self.msg_lens.get(str(gid)) or [])
                    if t0 - x[0] <= 12 and x[1] == str(uid)]
            short_cnt = len([1 for x in lens if x[2] <= short_chars])
            if short_cnt >= 2:
                d = max(d, burst_wait)
        except Exception:
            pass
        d = max(mn, min(mx, d))
        try:
            d *= random.uniform(1.0 - jit, 1.0 + jit)
        except Exception:
            pass
        return d

    @staticmethod
    def _split_by_marker(text: str, marker: str = "|||", max_parts: int = 24) -> list:
        """模型自己决定的分段：用 ||| 标注（兼容全角 ｜ 与中间夹空格）。"""
        import re as _re
        if not marker:
            return []
        t = str(text or "").replace("｜", "|")
        t = _re.sub(r"\|\s*(?=\|)", "|", t)          # 去掉标记之间的空格/换行
        t = _re.sub(r"\s*\|{2,}\s*", "|||", t)        # 两个及以上的竖线都当成分段标记
        if marker not in t:
            return []                                  # 没有标记 → 交回给默认拆条逻辑
        parts = [x.strip() for x in t.split(marker) if x.strip()]
        if len(parts) > max_parts:
            parts = parts[:max_parts - 1] + ["".join(parts[max_parts - 1:])]
        return parts

    @staticmethod
    def _split_by_lines(text: str, max_parts: int = 24) -> list:
        """模型用换行表达"这是几条消息"（比符号更自然）。"""
        import re as _re
        parts = [x.strip().strip("，,、；;：:") for x in _re.split(r"[\r\n]+", str(text or ""))]
        parts = [x for x in parts if x]
        if len(parts) > max_parts:
            parts = parts[:max_parts - 1] + [" ".join(parts[max_parts - 1:])]
        return parts if len(parts) > 1 else []

    @staticmethod
    def _char_parts(text: str, max_parts: int = 24) -> list:
        """逐字拆（标点跟在前一个字后面更像真人）。"""
        t = (text or "").strip()
        if not t:
            return []
        chars = []
        i = 0
        n = len(t)
        _tail = "._-&"
        while i < n:
            ch = t[i]
            if chars and ch in "，。！？!?、；;…~— ":
                chars[-1] += ch
                i += 1
                continue
            if ch.isalnum() and ord(ch) < 128:      # 英文单词/数字整块，别拆成 BigF + ish
                j = i + 1
                while j < n and ((t[j].isalnum() and ord(t[j]) < 128) or (
                        t[j] in _tail and j + 1 < n and t[j + 1].isalnum()
                        and ord(t[j + 1]) < 128)):
                    j += 1
                chars.append(t[i:j])
                i = j
                continue
            chars.append(ch)
            i += 1
        chars = [c for c in chars if c.strip()]
        if len(chars) > max_parts:                     # 超上限就把尾巴合成一条
            chars = chars[:max_parts - 1] + ["".join(chars[max_parts - 1:])]
        return chars

    @staticmethod
    def _safe_cut(t: str, pos: int) -> int:
        return _safe_cut(t, pos)

    @staticmethod
    def _split_text(text: str, max_parts: int = 5, min_chars: int = 12) -> list:
        """把一段回复按句子/逗号切成人话节奏的几段；切不动就按长度对半。"""
        import re as _re
        t = (text or "").strip()
        if len(t) < min_chars or max_parts < 2:
            return [t] if t else []
        segs = [x for x in _re.split(r"(?<=[。！？!?…~])\s*", t) if x.strip()]
        if len(segs) < 2:
            segs = [x for x in _re.split(r"(?<=[，,；;])\s*", t) if x.strip()]
        if len(segs) < 2:
            # 没有任何标点时按长度切：优先切在「小句开头」处（我/你/它/不过…），
            # 实在找不到再退回长度中点（并且绝不断英文单词）
            cut = 0
            _start = "我你他她它这那其但而所因不过所以然后而且还有就是"
            for _p in range(max(2, len(t) // 4), len(t) - 2):
                if t[_p] in _start and not (t[_p - 1].isalnum() and ord(t[_p - 1]) < 128):
                    cut = _p
                    break
            if not cut:
                cut = _safe_cut(t, max(1, len(t) // 2))
            segs = [t[:cut], t[cut:]]
        while len(segs) > max_parts:
            i = min(range(len(segs) - 1), key=lambda k: len(segs[k]) + len(segs[k + 1]))
            segs[i:i + 2] = [segs[i] + segs[i + 1]]
        # 拆成多条发的时候，别让每条尾巴挂着逗号（"对，" / "我是 DeepSeek，"）
        out = []
        for _s in segs:
            _s = _s.strip().strip("，,、；;：:")
            if _s:
                out.append(_s)
        return out

    def _spawn_parts(self, event, parts: list, delay: float = None, jitter: float = 0.3) -> None:
        """首条由 AstrBot 正常发出，其余几条稍后补发。
        每条间隔都带随机抖动 —— 固定节奏反而像机器。"""
        if not parts:
            return
        if delay is None:
            try:
                delay = float((self.cfg.get("split_reply") or {}).get("delay_ms", 700)) / 1000.0
            except Exception:
                delay = 0.7
        try:
            jitter = float(jitter if jitter is not None else 0.3)
        except Exception:
            jitter = 0.3

        async def _run():
            try:
                for p in parts:
                    d = delay
                    try:
                        d = max(0.15, delay * random.uniform(1.0 - jitter, 1.0 + jitter))
                    except Exception:
                        d = delay
                    await asyncio.sleep(d)
                    await event.send(MessageChain([Plain(p)]))
            except Exception as e:
                self._log("补发消息失败: %r" % (e,))
        try:
            if not hasattr(self, "_bg_tasks"):
                self._bg_tasks = set()
            t = asyncio.create_task(_run())
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)
        except Exception as e:
            self._log("补发任务创建失败: %r" % (e,))

    def _save_cfg(self) -> None:
        """写回 config.json：**先读盘再合并**，只覆盖内存里已知的键。

        历史教训：整份 self.cfg 直接写盘会把外部新增的配置块（kb/poke_reply/表情库参数）
        全部抹掉 —— 这里改成读-改-写，磁盘上多出来的键一律保留。
        """
        try:
            disk = {}
            try:
                with open(CONFIG_PATH, encoding="utf-8") as f:
                    disk = json.load(f) or {}
            except Exception:
                disk = {}
            _before = set(disk.keys())
            for k, v in (self.cfg or {}).items():
                if isinstance(v, dict) and isinstance(disk.get(k), dict):
                    merged = dict(disk[k])
                    merged.update(v)
                    disk[k] = merged
                else:
                    disk[k] = v
            _lost = _before - set(disk.keys())
            if _lost:
                self._log("⚠️ 写配置会丢键 %s → 已放弃这次写入（保护配置）" % sorted(_lost))
                return
            try:                                   # 写前留个时间戳备份，方便回溯
                if os.path.isfile(CONFIG_PATH):
                    import shutil
                    import time as _t
                    shutil.copy2(CONFIG_PATH, "%s.bak-%d" % (CONFIG_PATH, int(_t.time())))
                    import glob
                    _baks = sorted(glob.glob(CONFIG_PATH + ".bak-*"))
                    for _b in _baks[:-5]:
                        try:
                            os.remove(_b)
                        except Exception:
                            pass
            except Exception:
                pass
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(disk, f, ensure_ascii=False, indent=1)
            os.replace(tmp, CONFIG_PATH)
        except Exception as e:
            self._log("写配置失败: %r" % (e,))

    async def _ensure_group_card(self, gid: str, event) -> None:
        """进新群自动建人格卡：模板生成 + 注册 prompt_by_group / 白名单 / 记忆。"""
        try:
            ap = self.cfg.get("auto_persona") or {}
            if not ap.get("enabled", True) or not gid or gid == "0":
                return
            pb = self.cfg.get("prompt_by_group") or {}
            if str(gid) in pb:
                return                                   # 已有卡
            # 只对真实 QQ 号生效（测试连接不建卡）
            if str(self.cfg.get("allowed_self_id") or "") != str(event.get_self_id() or ""):
                return
            pdir = os.path.join(PLUGIN_DIR, "prompts", "personas")
            os.makedirs(pdir, exist_ok=True)
            card_path = os.path.join(pdir, "%s.txt" % gid)
            # 群名
            gname = ""
            try:
                info = await asyncio.wait_for(
                    event.bot.call_action("get_group_info", group_id=int(gid)), timeout=4)
                gname = str((info or {}).get("group_name") or "")
            except Exception:
                pass
            # 卡片内容：模板（没有就用默认温和版）
            tpl = os.path.join(PLUGIN_DIR, str(ap.get("template", "prompts/personas/_template.txt")))
            src = tpl if os.path.exists(tpl) else os.path.join(PLUGIN_DIR, "prompts", "system_prompt.txt")
            try:
                body = open(src, encoding="utf-8").read().strip()
            except Exception:
                body = ""
            with open(card_path, "w", encoding="utf-8") as f:
                f.write("[本群：%s｜建卡于 %s]\n\n%s\n" % (
                    gname or ("群" + gid[-4:]), time.strftime("%Y-%m-%d"), body))
            # 注册
            rel = os.path.relpath(card_path, PLUGIN_DIR)
            pb[str(gid)] = rel
            self.cfg["prompt_by_group"] = pb
            if ap.get("add_allowed", True):
                al = [str(x) for x in (self.cfg.get("allowed_groups") or [])]
                if gid not in al:
                    al.append(gid)
                self.cfg["allowed_groups"] = al
            if ap.get("add_memory", True):
                mg = [str(x) for x in (self.cfg.get("memory", {}).get("groups") or [])]
                if gid not in mg:
                    mg.append(gid)
                self.cfg.setdefault("memory", {})["groups"] = mg
            self._save_cfg()
            self.scorer.cfg = self.cfg
            # 登记册（管理入口）
            try:
                reg_p = os.path.join(MEM_DIR if False else PLUGIN_DIR, "data", "group_registry.json")
                os.makedirs(os.path.dirname(reg_p), exist_ok=True)
                reg = {}
                if os.path.exists(reg_p):
                    reg = json.load(open(reg_p, encoding="utf-8"))
                reg[str(gid)] = {"name": gname, "persona": rel, "created": int(time.time())}
                tmp = reg_p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(reg, f, ensure_ascii=False, indent=1)
                os.replace(tmp, reg_p)
            except Exception:
                pass
            self._log("新群建档：群 %s（%s）→ %s，已加入白名单/记忆" % (gid, gname or "?", rel))
        except Exception as e:
            self._log("自动建档失败(忽略): %r" % (e,))

    def _remember_bot_line(self, event, text: str) -> None:
        """把她自己（海龟汤模式）说的话记进群聊缓冲，别让 burst 把老消息拼进来。"""
        try:
            gid = str(event.get_group_id() or "")
            if not gid or gid == "0":
                return
            t = " ".join(str(text or "").split())
            if not t:
                return
            if len(t) > 60:
                t = t[:60] + "…"
            try:
                lines = int(self.cfg.get("context_lines", 6))
            except Exception:
                lines = 6
            buf = self.recent.get(gid)
            if buf is None:
                buf = deque(maxlen=max(2, lines))
                self.recent[gid] = buf
            buf.append(("我", t, ""))
            self.last_reply_ts[gid] = time.time()
            try:
                self.last_reply_to[gid] = str(event.get_sender_id() or "")
            except Exception:
                pass
        except Exception:
            pass

    async def _turtle_say(self, event, text: str, quote: bool = False) -> None:
        """海龟汤模式自己发消息（不走 LLM 链路）。按空行分成几条发；引用只挂第一条。"""
        parts = [p.strip() for p in str(text or "").split("\n\n") if p.strip()]
        if not parts:
            return
        self._remember_bot_line(event, " ".join(parts))
        mid = None
        if quote:
            try:
                mid = getattr(getattr(event, "message_obj", None), "message_id", None)
            except Exception:
                mid = None
        for i, p in enumerate(parts):
            if i:
                await asyncio.sleep(0.25)
            chain = []
            # 引用对方那条消息。注意：测试客户端给的 message_id 是 3 位短号，挂引用会静默发送失败，
            # 所以只对"看起来像真实 QQ 消息号"（≥6 位）的才引用。
            if mid and i == 0 and len(str(mid).lstrip("-")) >= 6:
                chain.append(Reply(id=str(mid)))
            chain.append(Plain(p))
            try:
                await event.send(MessageChain(chain))
            except Exception as se:
                self._log("海龟汤发送失败(去掉引用重试): %r" % (se,))
                try:
                    await event.send(MessageChain([Plain(p)]))
                except Exception:
                    pass

    def _spawn_pregen(self, cfg: dict, g: dict, category: str = "") -> None:
        """后台预生成一道题（不打扰群）。同一时间只跑一个，失败就算了。"""
        if getattr(self, "_pregen_busy", False):
            return
        _g2 = g or {}
        if len(turtle_puzzles.ai_puzzles()) >= int(_g2.get("max_ai", 120) or 120):
            self._log_debug("海龟汤：AI 题库已经囤够了，不预生成")
            return
        _bud = int(_g2.get("budget", 30000) or 30000)
        _maxday = int(_g2.get("max_per_day", 3) or 0)

        async def _run():
            self._pregen_busy = True
            try:
                pz = await asyncio.to_thread(turtle_gen.quick_one, cfg, 4, _bud, _maxday, category or None)
                if pz:
                    self._log("海龟汤：后台预生成成功 %s《%s》" % (pz.get("id"), pz.get("title")))
                else:
                    self._log("海龟汤：后台预生成没成功（不影响玩）")
            except Exception as e:
                self._log("海龟汤：后台预生成出错 %r" % (e,))
            finally:
                self._pregen_busy = False

        try:
            asyncio.get_event_loop().create_task(_run())
        except Exception as e:
            self._log("海龟汤：预生成任务没起来 %r" % (e,))

    async def _turtle_handle(self, event, text: str, gid: str, cfg: dict) -> bool:
        """海龟汤模式总入口。返回 True = 本条已处理（或已拦截），DS 链路不会触发。"""
        try:
            # 先铺一层默认值，再盖上配置——配置里没写的项（比如新加的换题词）也有兜底
            tc = dict(DEFAULTS.get("turtle") or {})
            tc.update(cfg.get("turtle") or {})
            # 每群可覆盖（例如豹群：只认 @、状态不落盘）
            pg = ((tc.get("per_group") or {}).get(str(gid))) or {}
            if pg:
                tc.update(pg)
            no_disk = bool(tc.get("no_disk"))
            if not tc.get("enabled", True):
                return False
            gl = [str(x) for x in (tc.get("groups") or [])]
            if gl and str(gid) not in gl:
                return False
            # 一局游戏按「群」记（私聊则按会话）
            key = str(gid or getattr(event, "unified_msg_origin", "") or "")
            if not key:
                return False
            sess = turtle_session.load(key, no_disk)
            t = str(text or "").strip()
            at_real = self._at_me(event)            # 真正 @她（At 组件里就是她的 QQ）
            at_me = at_real or self._quotes_me(event)  # 宽松些：引用她也算

            # ① 揭晓 / 退出（严格模式下同样要求 @她）
            _strict_exit = str(tc.get("question_filter") or "").lower() == "at"
            _exit_kw = next((k for k in (tc.get("exit_keywords") or []) if k and k in t), "")
            _exit_ok = bool(_exit_kw) and len(t) <= 12 and not any(x in t for x in ("吗", "?", "？"))
            if sess and _exit_ok and (at_real if _strict_exit else True):
                pz = turtle_puzzles.by_id(sess.get("puzzle_id")) or turtle_puzzles.get(0)
                msg = str(tc.get("reveal") or "【汤底】{bottom}").format(
                    bottom=pz.get("bottom", ""), surface=pz.get("surface", ""), title=pz.get("title", ""))
                turtle_session.finish(key, str(pz.get("id") or ""), no_disk)
                tail = str(tc.get("after_hint") or "")
                await self._turtle_say(event, msg + tail, quote=bool(tc.get("quote", True)))
                self._log("海龟汤：揭晓并退出会话 %s" % key)
                event.stop_event()
                return True

            # ② 开局 / 换题 / 再看一眼汤面 / 改难度
            _strict = str(tc.get("question_filter") or "").lower() == "at"
            _fresh = bool(sess) and (int(time.time()) - int(sess.get("started_at") or 0)
                                     <= int(tc.get("stale_sec", 1500) or 1500))
            _kw_start = any(k and k in t for k in (tc.get("start_keywords") or []))
            # 换题：只认短句，问句里出现「换一个」这种不算（例如「他是不是换个人」）
            _chg_kw = next((k for k in (tc.get("change_keywords") or []) if k and k in t), "")
            _kw_change = bool(_chg_kw) and len(t) <= 12 \
                and not any(x in t for x in ("吗", "?", "？", "是不是"))
            # 「汤面」要的是纯索取（「汤面」「看下汤面」），不能把「汤面里有音乐吗」这种提问吃掉
            _sf_kw = next((k for k in (tc.get("surface_keywords") or []) if k and k in t), "")
            _kw_surface = bool(_sf_kw) and len(t.replace(_sf_kw, "").strip("　 ，。、！？!?～~…")) <= 4
            # 点名类别：「来个恐怖悬疑的海龟汤」/ 局中「换一个温情的」
            _cat = turtle_puzzles.detect_category(t)
            _cat_label = turtle_puzzles.cat_label(_cat) if _cat else ""
            msg_tpl = str(tc.get("opening") or "海龟汤开局！\n\n【汤面】{surface}")
            # 开局要不要 @：没显式配置时——严格模式的群（豹群）要，其他群不用
            _need_at = tc.get("start_needs_at")
            if _need_at is None:
                _need_at = _strict
            _loose = any(k and k in t for k in (tc.get("loose_start_keywords") or []))
            _ok_meta = at_real if _need_at else True            # 局内操作（换题/看汤面/改难度）
            _ok_start = at_real if _need_at else bool(at_me or _loose)   # 开新局
            # 难度：群里可以点「简单点的海龟汤」/「来个困难海龟汤」
            _diff_sel = str(tc.get("difficulty") or "hard").lower()
            if any(k and k in t for k in (tc.get("easy_words") or [])):
                _diff_sel = "easy"
            elif any(k and k in t for k in (tc.get("hard_words") or [])):
                _diff_sel = "hard"
            _has_diff_word = any(k and k in t for k in (tc.get("easy_words") or [])
                                 + (tc.get("hard_words") or []))
            # 「简单点」「困难点」= 改难度；「答案很简单吧」这种提问不算
            _pure_diff = (len(t) <= 8 and _has_diff_word
                          and not any(x in t for x in ("吗", "?", "？", "吧", "呢", "么"))
                          and (len(t) <= 4 or any(w in t for w in ("点", "来", "要", "改", "调", "玩"))))
            if _pure_diff and _fresh:
                sess["difficulty"] = _diff_sel
                turtle_session.save(key, sess, no_disk)
                self._log("海龟汤：%s 难度改成 %s" % (key, _diff_sel))
                event.stop_event()
                return True
            # 局中点名要某类还得"像个请求"才行（「来个恐怖的」/「换个温情的」），
            # 不然「这是本格推理还是变格推理」这种提问会被当成换题、把局掀了
            _cat_req = bool(_cat) and len(t) <= 24 and bool(re.search(
                r"(来个|来一个|来一道|来点|来份|来碗|换一个|换个|换道|换点|想玩|想要|要个|要一道|"
                r"整一个|整道|求个|求一|给我来|安排|开一局)", t))
            if _cat_req and _fresh and not _kw_surface and not _kw_start:
                _kw_change = True
            if sess and not _fresh:
                _trigger = bool(_kw_start)      # 弃局了：想再开还得喊「海龟汤/开局」
            elif _fresh:
                _trigger = bool(_kw_start or _kw_change or _kw_surface or _cat_req)
            else:
                _trigger = bool(_kw_start)      # 没在玩：只有「海龟汤/开局」才进这个模式
            _ok_now = _ok_meta if _fresh else _ok_start
            if _trigger and _ok_now and (_kw_start or _cat_req or _kw_change) and bool(tc.get("ai_gate", True)):
                # 关键词太容易误伤（"我想知道这个海龟汤要猜到什么才算结束"也会命中"海龟汤"）
                # → 交给一次很小的 AI 判定：是不是真的要开局/换题
                try:
                    _recent_txt = " ｜ ".join([x[1] for x in list(self.recent.get(str(gid)) or [])[-4:]])
                    _ai, _aiu = await asyncio.to_thread(turtle_judge.wants_turtle, t, _recent_txt, cfg)
                    self._log("海龟汤：开局判定 %r → start=%s cat=%s diff=%s why=%s（%s token）"
                              % (t[:20], _ai.get("start"), _ai.get("category"), _ai.get("difficulty"),
                                 _ai.get("why"), (_aiu or {}).get("total_tokens")))
                    if not _ai.get("start"):
                        self._log("海龟汤：AI 判断不是要开局 → 不拦这条，交给正常流程")
                        return False
                    if str(_ai.get("category") or "") in turtle_puzzles.CATEGORIES:
                        _cat = str(_ai["category"])
                        _cat_label = turtle_puzzles.cat_label(_cat)
                        if _fresh:
                            _kw_change = True
                    if str(_ai.get("difficulty") or "") in ("easy", "hard"):
                        _diff_sel = str(_ai["difficulty"])
                except Exception as e:
                    self._log("海龟汤：开局判定失败（%r），按关键词老规则走" % (e,))
            if _trigger and _ok_now:
                # (a) 局中只想再看一眼汤面 → 把当前那道原样发一遍，不重开
                if _kw_surface and _fresh and not _kw_change:
                    pz = turtle_puzzles.by_id(sess.get("puzzle_id")) or {}
                    msg = msg_tpl.format(surface=pz.get("surface", ""), title=pz.get("title", ""),
                                         category=turtle_puzzles.cat_label(pz.get("category")))
                    await self._turtle_say(event, msg, quote=bool(tc.get("quote", True)))
                    self._log("海龟汤：%s 重发汤面（题目=%s）" % (key, sess.get("puzzle_id")))
                    event.stop_event()
                    return True
                # (b) 局中又喊「海龟汤」→ 不重开、不刷屏（想换题请说「换一个」/「来个恐怖的」）
                if _kw_start and not _kw_change and _fresh:
                    self._log("海龟汤：%s 已在局中，忽略重复的开局请求" % key)
                    event.stop_event()
                    return True
                # (c) 出新题：这个群一年内出过的题不再出（换题时也排掉当前这道）
                try:
                    _cd = int(float(tc.get("reuse_cooldown_days", 365) or 0) * 86400)
                except Exception:
                    _cd = 365 * 86400
                _avoid = [sess.get("puzzle_id")] if (_kw_change and sess.get("puzzle_id")) else []
                _use_web = bool(tc.get("use_web", True))
                _all = turtle_puzzles.all_puzzles(include_web=_use_web)
                pz, _recycled = turtle_session.pick_puzzle(
                    key, _all, cooldown_sec=_cd, avoid=_avoid, category=_cat)
                _g = tc.get("gen") or {}
                _on_demand = bool(_g.get("on_demand", True))
                if (pz is None or _recycled) and _on_demand:
                    # 没了现货（这个类别没出过 / 都进冷却期了）→ 现编一道（子线程里跑，不卡事件循环）
                    self._log("海龟汤：%s %s没有现货了，现编一道" % (key, ("[%s] " % _cat_label) if _cat else ""))
                    _hs = str(_g.get("headsup") or "题库里的题你们都刷过啦，我现编一道新的，稍等半分钟……")
                    if _cat and pz is None:
                        _hs = "这类题我手头还没有呢，我现编一道%s的，稍等半分钟……" % _cat_label
                    await self._turtle_say(event, _hs, quote=bool(tc.get("quote", True)))
                    try:
                        _tries = int(_g.get("tries", 4) or 4)
                        _bud = int(_g.get("budget", 30000) or 30000)
                        _maxday = int(_g.get("max_per_day", 3) or 0)
                        _new = await asyncio.to_thread(
                            turtle_gen.quick_one, cfg, _tries, _bud, _maxday, _cat or None)
                    except Exception as e:
                        self._log("海龟汤：现编出错 %r" % (e,))
                        _new = None
                    if _new:
                        pz, _recycled = _new, False
                        self._log("海龟汤：现编成功 %s《%s》[%s]"
                                  % (pz.get("id"), pz.get("title"), turtle_puzzles.cat_label(pz.get("category"))))
                    elif pz is not None:
                        self._log("海龟汤：现编没成功，兜底重出最久以前那道（%s）" % pz.get("id"))
                    else:
                        # 这个类别一道都没有、又没编出来 → 退回到任意类别最久以前那道
                        pz, _recycled = turtle_session.pick_puzzle(key, _all, cooldown_sec=_cd)
                        self._log("海龟汤：这个类别没编出来，只好拿别类的旧题兜底（%s）" % (pz or {}).get("id"))
                if pz is None:      # 兜底也没题（题库空）→ 别把整条消息吞了
                    return False
                if _recycled:
                    self._log("海龟汤：%s 的题库已经在冷却期内轮完了，只好重出最久以前那道（%s）"
                              % (key, pz.get("id")))
                turtle_session.mark_used(key, pz.get("id"))
                # 存货不多了 → 后台悄悄提前补一道，免得下次又要等
                if _on_demand:
                    try:
                        _left = turtle_session.fresh_count(
                            key, turtle_puzzles.all_puzzles(include_web=_use_web), _cd, _cat)
                        if _left <= int(_g.get("pregen_when_left", 3) or 0):
                            self._spawn_pregen(cfg, _g, _cat)
                    except Exception:
                        pass
                turtle_session.start(key, pz, no_disk)
                sess = turtle_session.load(key, no_disk) or {}
                sess["last_open_ts"] = int(time.time())
                sess["difficulty"] = _diff_sel
                sess["dry"] = 0
                sess["points"] = []
                sess["guided"] = False
                turtle_session.save(key, sess, no_disk)
                msg = msg_tpl.format(surface=pz.get("surface", ""), title=pz.get("title", ""),
                                     category=turtle_puzzles.cat_label(pz.get("category")),
                                     difficulty=("简单" if str(sess.get("difficulty") or "hard") == "easy" else "困难"))
                await self._turtle_say(event, msg, quote=bool(tc.get("quote", True)))
                _why = ("换题词=%s" % _chg_kw) if _kw_change else (
                    ("类别=%s" % _cat) if _cat else ("开局词=%s" % _kw_start))
                try:
                    _pts, _pu = await asyncio.to_thread(turtle_judge.split_points, pz, cfg)
                    if _pts:
                        sess["points"] = _pts
                        turtle_session.save(key, sess, no_disk)
                        self._log("海龟汤：%s 要点拆成 %d 条：%s" % (key, len(_pts), " / ".join(_pts)))
                except Exception as e:
                    self._log("海龟汤：拆要点失败（忽略）%r" % (e,))
                self._log("海龟汤：%s会话 %s，题目=%s[%s]（触发：%s）"
                          % ("换题" if _kw_change else "开局", key, pz.get("id"),
                             turtle_puzzles.cat_label(pz.get("category")), _why))
                event.stop_event()
                return True

            # ③ 刚玩完的"余温"：这局刚结束不久，消息像是在追问这道题 → 直接用 GLM 解释（不走 DS）
            if not sess and not any(k and k in t for k in (tc.get("start_keywords") or [])):
                _win = int(tc.get("afterglow_sec", 1800) or 1800)
                fin = turtle_session.load_finished(key, _win, no_disk)
                _ask_ok = at_real if _strict_exit else at_me
                if fin and _ask_ok:
                    _ask = any(k in t for k in ("汤底", "谜底", "答案", "解释", "没看懂", "看不懂",
                                               "为什么", "啥意思", "什么意思", "怎么回事", "讲讲",
                                               "讲一下", "详细说"))
                    fpz = turtle_puzzles.by_id(fin.get("puzzle_id")) or {}
                    if _ask and not fpz:
                        # 题库换过、找不到那道题了 → 干脆不接话（也免得走 DS 答成词典）
                        self._log("海龟汤余温：找不到题目 %r，静默" % (fin.get("puzzle_id"),))
                        event.stop_event()
                        return True
                    if _ask:
                        try:
                            _hist = "\n".join(
                                "%s → %s" % (str(r.get("q") or "")[:50],
                                             str(r.get("a") or "").replace("\n", " / ")[:30])
                                for r in (fin.get("log") or fin.get("qa") or []))
                            txt, usage = turtle_judge.explain(fpz, t, cfg, history=_hist)
                            self._log("海龟汤余温答疑 %s：%r → %s（输入 %s + 输出 %s token）"
                                      % (key, t[:20], str(txt)[:30],
                                         (usage or {}).get("prompt_tokens"),
                                         (usage or {}).get("completion_tokens")))
                        except Exception as e:
                            self._log("海龟汤余温答疑失败: %r" % (e,))
                            txt = ""
                        if txt:
                            await self._turtle_say(event, txt, quote=bool(tc.get("quote", True)))
                        event.stop_event()
                        return True

            # ④ 游戏中：判题（GLM）
            if sess:
                mode = str(tc.get("question_filter") or ("at" if tc.get("at_only") else "all")).lower()
                if mode == "at" and not at_real:
                    event.stop_event()       # 严格：只有真正 @她 才算提问
                    return True
                if mode == "at_quote" and not at_me:
                    event.stop_event()       # @她 或 引用她
                    return True
                if mode == "llm":
                    # 第一层：本地预筛（0 token）—— 纯语气词/表情/超短反应直接跳过
                    if _TURTLE_REACT.match(t or "") or len(t) <= 2:
                        self._log_debug("海龟汤：本地预筛跳过 %r" % t[:16])
                        event.stop_event()
                        return True
                    if at_me and len(t) <= 30:
                        t = t  # 被 @ 了就直接当提问，交给 GLM 再确认（不额外花钱）
                pz = turtle_puzzles.by_id(sess.get("puzzle_id")) or turtle_puzzles.get(0)
                # 难度：困难档默认一个字都不多给；连着「不重要」太多、或玩家开口讨提示，才给一句
                _diff = str(sess.get("difficulty") or tc.get("difficulty") or "hard").lower()
                _ask_hint = any(k and k in t for k in (tc.get("hint_ask_words") or []))
                _dry = int(sess.get("dry") or 0)
                _allow_hint = bool(_ask_hint or _diff != "hard"
                                   or _dry >= int(tc.get("hint_after_dry", 5) or 5))
                _hint_chars = int(tc.get("easy_hint_chars" if _diff != "hard" else "hard_hint_chars",
                                      25 if _diff != "hard" else 16) or 0)
                _ask_progress = any(k and k in t for k in (tc.get("progress_words") or []))
                if _ask_progress and self._pts_all_ok(sess):
                    try:
                        _sum, _su = await asyncio.to_thread(
                            turtle_judge.summarize, pz, turtle_session.fmt_log(sess),
                            sess.get("points") or [], sess.get("covered") or [], cfg)
                        self._log("海龟汤看进度：%d 字（%s token）"
                                  % (len(_sum or ""), (_su or {}).get("total_tokens")))
                    except Exception as e:
                        self._log("海龟汤看进度失败: %r" % (e,))
                        _sum = ""
                    if _sum:
                        await self._turtle_say(event, _sum, quote=bool(tc.get("quote", True)))
                    event.stop_event()
                    return True
                if _ask_hint:
                    # 玩家明确讨提示：单独给一句方向，不回答「是/不是/不重要」，也不再卡关键词
                    try:
                        who = ""
                        try:
                            who = str(event.get_sender_name() or event.get_sender_id() or "")
                        except Exception:
                            pass
                        _htxt, _hu = turtle_judge.suggest(
                            pz, t, facts=turtle_session.fmt_facts(sess),
                            history=turtle_session.fmt_history(sess),
                            asked=turtle_session.fmt_asked(sess), cfg=cfg,
                            limit=int(tc.get("asked_hint_chars", 20) or 20))
                        self._log("海龟汤讨提示：%r → %s（输入 %s + 输出 %s token）"
                                  % (t[:20], str(_htxt)[:30], (_hu or {}).get("prompt_tokens"),
                                     (_hu or {}).get("completion_tokens")))
                    except Exception as e:
                        self._log("海龟汤讨提示失败: %r" % (e,))
                        _htxt = "再想想汤面里最反常的那个细节。"
                    if _htxt:
                        sess["dry"] = 0
                        sess = turtle_session.add_qa(key, sess, who, t[:80], "（讨提示）" + _htxt,
                                                     no_disk=no_disk)
                        await self._turtle_say(event, _htxt, quote=bool(tc.get("quote", True)))
                    event.stop_event()
                    return True
                try:
                    ans, usage = turtle_judge.answer(
                        pz, t, facts=turtle_session.fmt_facts(sess),
                        history=turtle_session.fmt_history(sess), cfg=cfg,
                        difficulty=_diff, allow_hint=_allow_hint, hint_request=_ask_hint,
                        hint_chars=_hint_chars)
                    self._log("海龟汤判题：%r → %s（输入 %s + 输出 %s token）"
                              % (t[:20], ans.replace("\n", " / "),
                                 (usage or {}).get("prompt_tokens"), (usage or {}).get("completion_tokens")))
                except Exception as e:
                    self._log("海龟汤判题失败: %r" % (e,))
                    ans = "不重要"
                if re.search(r"(重要吗|重要不重要|是否重要|有关系吗|有关系不|有关吗|必须知道吗)", t) \
                        and str(ans or "").splitlines()[0].strip() == "不重要":
                    ans = "不是\n这点跟真相无关，换个方向问。"
                    self._log("海龟汤：相关性问题却不能回『不重要』→ 本地改成『不是』")
                _solved = any("猜出来了" in ln for ln in str(ans or "").splitlines()[1:])
                if not _solved and self._looks_solved(t, pz.get("bottom", "")):
                    # 玩家（可能一口气）把汤底说出来了，模型没打标记也照样算猜出来
                    _solved = True
                    _first = str(ans or "").splitlines()[0].strip() if str(ans or "").strip() else ""
                    ans = (_first if _first in ("是", "不是", "不重要", "是也不是") else "是") + "\n猜出来了"
                    self._log("海龟汤：玩家这段话与汤底高度重合 → 判为猜出来，直接揭晓")
                # 换个说法又问了一遍同一个问题 → 必须跟之前一致（免得她自己打自己脸）
                # 「不重要」滥用：一律再过一次复核（汤底能推出就必须给是/不是）
                try:
                    _nf0 = str(ans or "").splitlines()[0].strip()
                    if _nf0 == "不重要" and not _solved:
                        _v, _vu = await asyncio.to_thread(
                            turtle_judge.verify, pz, t, "不重要",
                            turtle_session.fmt_facts(sess), turtle_session.fmt_history(sess), cfg)
                        if _v:
                            self._log("海龟汤：『不重要』复核 → %s（%s token）" % (_v, (_vu or {}).get("total_tokens")))
                            ans = _v if _v == "不重要" else _v + (
                                "\n这点跟真相无关。" if False else "")
                except Exception as _e:
                    self._log("海龟汤：复核失败（忽略）%r" % (_e,))
                if not _solved:
                    try:
                        _pq, _pa = turtle_session.similar_qa(sess, t, 0.6)
                        _of = str(_pa or "").splitlines()[0].strip() if _pa else ""
                        _nf = str(ans or "").splitlines()[0].strip()
                        if _of in ("是", "不是", "不重要", "是也不是") and _nf and _nf != _of:
                            # 先让模型仲裁"这两个问题是不是一回事"（DeepSeek 下一次 ~20 token）
                            _fin = ""
                            try:
                                # 以【汤底】为准复核：之前答错了就纠正（不再盲从旧答案）
                                _fin, _fu = await asyncio.to_thread(
                                    turtle_judge.verify, pz, t, _nf,
                                    turtle_session.fmt_facts(sess),
                                    "老问题：%s → %s" % (_pq[:40], _of), cfg)
                            except Exception as _e:
                                self._log("海龟汤：一致性复核失败（%r），保留本次答案" % (_e,))
                            if _fin:
                                self._log("海龟汤：同类问题复核 老=%r 新=%r → 最终=%r（老问题=%r）"
                                          % (_of, _nf, _fin, _pq[:16]))
                                ans = _fin
                                # 复核说旧的答错了 → 把历史里那条也改过来，别让玩家看到自相矛盾
                                if _fin != _of:
                                    try:
                                        for _r in (sess.get("qa") or []):
                                            if str(_r.get("q") or "").strip() == str(_pq).strip():
                                                _r["a"] = _fin
                                                break
                                        turtle_session.save(key, sess, no_disk)
                                        self._log("海龟汤：历史答案已纠正 %r：%r → %r"
                                                  % (_pq[:16], _of, _fin))
                                    except Exception as _e2:
                                        self._log("海龟汤：纠正历史失败 %r" % (_e2,))
                            else:
                                self._log("海龟汤：复核没结果，保留本次答案 %r（旧=%r）" % (_nf, _of))
                    except Exception:
                        pass
                if str(ans).strip().startswith("跳过"):
                    self._log("海龟汤：GLM 判断不是提问 → 不吭声（%r）" % t[:20])
                    event.stop_event()
                    return True
                who = ""
                try:
                    who = str(event.get_sender_name() or event.get_sender_id() or "")
                except Exception:
                    pass
                _first = str(ans or "").splitlines()[0].strip()
                if _allow_hint:
                    sess["dry"] = 0          # 这次已经给过机会了 → 重新数，别连着送提示
                elif _first == "不重要":
                    sess["dry"] = _dry + 1
                else:
                    sess["dry"] = 0
                if _allow_hint and len(str(ans or "").splitlines()) > 1:
                    self._log("海龟汤：%s 给了一次提示（连错 %d 次后）: %s"
                              % (key, _dry, str(ans).splitlines()[1][:20]))
                sess = turtle_session.add_qa(key, sess, who, t[:80], ans, no_disk=no_disk)
                if int(sess.get("asked", 0)) % max(1, int(tc.get("fact_every", 10) or 10)) == 0:
                    try:
                        fs = turtle_judge.compress_facts(turtle_session.fmt_history(sess), cfg)
                        if fs:
                            sess["facts"] = fs
                            turtle_session.save(key, sess, no_disk)
                            self._log("海龟汤：已确认事实压缩为 %d 条" % len(fs))
                    except Exception:
                        pass
                if _solved:
                    # 玩家把汤底说全了 → 回「是 / 猜出来了」，然后直接把汤底揭晓收尾，别让人干等
                    _msg = str(tc.get("reveal") or "【汤底】{bottom}").format(
                        bottom=pz.get("bottom", ""), surface=pz.get("surface", ""),
                        title=pz.get("title", ""))
                    _tail = str(tc.get("after_hint") or "")
                    turtle_session.finish(key, str(pz.get("id") or ""), no_disk)
                    await self._turtle_say(event, ans, quote=bool(tc.get("quote", True)))
                    await self._turtle_say(event, _msg + _tail, quote=False)
                    self._log("海龟汤：玩家猜出来了，直接揭晓并收尾 %s" % key)
                    event.stop_event()
                    return True
                try:
                    _d = max(0.0, float(tc.get("answer_delay_ms", 1200) or 0) / 1000.0)
                    if _d:
                        await asyncio.sleep(_d * random.uniform(0.7, 1.3))
                except Exception:
                    pass
                await self._turtle_say(event, ans, quote=bool(tc.get("quote", True)))
                # 每 N 问评估一次"猜到哪了"，够接近就引导玩家收尾
                try:
                    _pts = sess.get("points") or []
                    _asked = int(sess.get("asked", 0))
                    _every = max(1, int(tc.get("progress_every", 3) or 3))
                    if _pts and (_asked % _every == 0 or _ask_hint):
                        _pg, _pgu = await asyncio.to_thread(
                            turtle_judge.progress, pz, _pts, turtle_session.fmt_asked(sess), cfg)
                        _new_cov = set(int(i) for i in (_pg.get("covered") or []) if str(i).isdigit())
                        _old_cov = set(int(i) for i in (sess.get("covered") or []) if str(i).isdigit())
                        # 只进不退：模型每次是重新判的，不并集会出现"1 个要点→2 个要点"的倒退
                        _cov = set(i for i in (_old_cov | _new_cov) if 0 <= i < len(_pts))
                        if _cov != _old_cov:
                            sess["covered"] = sorted(_cov)
                            turtle_session.save(key, sess, no_disk)
                        _left = len(_pts) - len(_cov)
                        self._log("海龟汤：%s 进度 %d/%d 要点（%s token）"
                                  % (key, len(_pts) - _left, len(_pts), (_pgu or {}).get("total_tokens")))
                        if _left <= 0 and not sess.get("guided"):
                            sess["guided"] = True
                            turtle_session.save(key, sess, no_disk)
                            await self._turtle_say(
                                event, "要点基本都猜出来了，差不多了——把真相串起来说一遍，"
                                       "或者说「揭晓」我就把汤底给你。", quote=False)
                        elif 0 < _left <= 2:
                            await self._turtle_say(
                                event, "还剩 %d 个要点没猜出来，接近真相了。" % _left, quote=False)
                except Exception as e:
                    self._log("海龟汤：进度评估失败（忽略）%r" % (e,))
                event.stop_event()
                return True
        except Exception as e:
            import traceback as _tb
            self._log("海龟汤处理异常: %r | %s" % (e, _tb.format_exc()[-200:].replace("\n", " ")))
            return False
        return False

    def _memory_block(self, gid: str, uid: str, last_reply_to: str = "", text: str = "") -> str:
        """读记忆产物拼成一段背景（只读文件，不做任何生成，保证对话链路快）。"""
        try:
            m = self.cfg.get("memory") or {}
            if not m.get("enabled") or str(gid or "") not in [str(x) for x in (m.get("groups") or [])]:
                return ""
            inj = m.get("inject") or {}
            out = []
            if inj.get("group_profile", True):
                gp = mem_store.read_profile(gid)
                if gp:
                    out.append("[本群长期印象（背景资料，可能过时，只作参考：不要照搬、不要主动提起你记过什么）]\n" + gp)
            if inj.get("members", True):
                md = (mem_store.read_members(gid).get("members") or {})
                want = [str(uid or ""), str(last_reply_to or "")]
                # 消息里点名提到的人（"你觉得某某怎么样"）也要带上，否则她手里没料只能打太极
                low = " ".join(str(text or "").split()).lower()
                if low:
                    for u, v in md.items():
                        nm = str(v.get("name") or "").strip()
                        if len(nm) >= 2 and nm.lower() in low and u not in want:
                            want.append(u)
                lines, seen = [], set()
                full = str(gid or "") in [str(x) for x in (inj.get("full_groups") or [])]
                if full:
                    # 全量模式：把所有人按发言量排序注入，直到用满字数预算
                    budget = int(inj.get("full_max_chars", 1200))
                    used = 0
                    for u, v in sorted(md.items(), key=lambda kv: -int(kv[1].get("msg_count", 0))):
                        if not v.get("profile"):
                            continue
                        line = "%s：%s" % (v.get("name") or u, v.get("profile"))
                        if used + len(line) > budget:
                            break
                        lines.append(line)
                        used += len(line) + 1
                for u in ([] if full else want):
                    u = str(u or "").strip()
                    if not u or u in seen:
                        continue
                    seen.add(u)
                    v = md.get(u)
                    if v and v.get("profile"):
                        lines.append("%s：%s" % (v.get("name") or u, v.get("profile")))
                    if len(lines) >= int(inj.get("max_members", 2)):
                        break
                if lines:
                    out.append("[群友印象（背景资料，可以自然地聊到，比如他平时爱聊什么、什么说话风格；"
                                "但不许照抄对方的原话、不许说负面评价或贴标签、不许提「我记着/我有记录」这类话；"
                                "没印象的人就当陌生人，别装熟）]\n" + "\n".join(lines))
            return "\n\n".join(out)
        except Exception as e:
            self._log("读记忆文件失败: %r" % (e,))
            return ""

    def _prompt_path(self, gid: str) -> str:
        """人格卡路径（群/私聊）—— 统一由 promptlib 解析，旧写法继续兼容。"""
        key = str(gid or "")
        private = (not key or key in ("0", "private"))
        try:
            path, _src = promptlib.pick_file(PLUGIN_DIR, self.cfg,
                                             "" if private else key, private)
            if path:
                return path
        except Exception:
            pass
        try:
            name = (self.cfg.get("prompt_by_group") or {}).get(key) \
                or self.cfg.get("system_prompt_file") or "system_prompt.txt"
        except Exception:
            name = "system_prompt.txt"
        name = str(name)
        if "/" not in name:
            name = "prompts/" + name
        return os.path.join(PLUGIN_DIR, name)

    def _touch(self, gid: str, mid=None) -> None:
        """记录该群最新一条消息（id + 时间），用来判断"我要回的那条是不是已经沉上去了"。"""
        if not gid or gid == "0":
            return
        now = time.time()
        buf = self.msg_times.get(gid)
        if buf is None:
            buf = deque(maxlen=80)
            self.msg_times[gid] = buf
        buf.append(now)
        while buf and now - buf[0] > 600:
            buf.popleft()
        if mid:
            self.last_msg[gid] = (str(mid), now)

    def _remember_reply(self, event: AstrMessageEvent) -> None:
        """把机器人自己发出去的话也记进群聊缓冲——不然她看不见自己刚说过什么，就会无限复读。"""
        try:
            gid = str(event.get_group_id() or "")
            if not gid or gid == "0":
                return
            result = event.get_result()
            text = ""
            for c in getattr(result, "chain", None) or []:
                if isinstance(c, Plain):
                    text += c.text
            text = " ".join((text or "").split())
            if not text:
                return
            if self._in("no_context_groups", gid):
                self.last_reply_ts[gid] = time.time()
                self.last_reply_to[gid] = str(event.get_sender_id() or "")
                return
            if len(text) > 60:
                text = text[:60] + "…"
            buf = self.recent.get(gid)
            if buf is None:
                buf = deque(maxlen=max(2, int(self.cfg.get("context_lines", 6))))
                self.recent[gid] = buf
            buf.append(("我", text, ""))
            try:
                t_in = self._t_in.pop(event.unified_msg_origin, 0.0)
                if t_in:
                    self._log("耗时: 收消息→回复完成 总 %.2fs" % (time.time() - t_in))
            except Exception:
                pass
            self.last_reply_ts[gid] = time.time()      # 供打分器判断"连续对话"
            try:
                self.last_reply_to[gid] = str(event.get_sender_id() or "")
            except Exception:
                pass
            self._log_debug("remember_reply: %s" % text[:30])
        except Exception as e:
            self._log("remember_reply ERROR: %r" % (e,))

    # ---------------- agent 工具层（模型输出=思考，动作靠调用工具） ----------------
    def _agent_cfg(self) -> dict:
        c = dict(self.cfg.get("agent") or {})
        c.setdefault("enabled", False)
        c.setdefault("send_tools", False)
        c.setdefault("send_tools_groups", [])
        c.setdefault("rescue_text", False)
        c.setdefault("session_ttl", 300)
        return c

    def _agent_send_allowed(self, event) -> bool:
        """这个会话允不允许"用工具说话"（名单外照旧用正文说话）。"""
        c = self._agent_cfg()
        if not c.get("send_tools"):
            return False
        groups = [str(x) for x in (c.get("send_tools_groups") or [])]
        return (not groups) or (self._chat_key(event) in groups)

    def _search_on(self) -> bool:
        """联网搜索开关（config: search.enabled）。"""
        try:
            return bool((self.cfg.get("search") or {}).get("enabled", True))
        except Exception:
            return False

    def _agent_tool_specs(self) -> list:
        c = self._agent_cfg()
        if not c.get("enabled"):
            return []
        try:
            return agent.tools.spec_list(c.get("tools") or {}, send_tools=bool(c.get("send_tools")))
        except Exception as e:
            self._log("agent：工具清单生成失败 %r" % (e,))
            return []

    def _ensure_agent_tools(self) -> None:
        """确保工具已注册（幂等：工具数没变就不重复注册）。"""
        if not self._agent_cfg().get("enabled"):
            return
        n = len(self._agent_tool_specs())
        if not n or getattr(self, "_agent_reg", None) == n:
            return
        if not hasattr(self, "_agent_sessions"):
            self._agent_sessions = {}
        self._register_agent_tools()
        self._agent_reg = n

    def _register_agent_tools(self) -> None:
        specs = self._agent_tool_specs()
        ctx = getattr(self, "context", None)
        if not specs or ctx is None or not hasattr(ctx, "register_llm_tool"):
            return
        for name, args, desc, mname in specs:
            try:
                ctx.register_llm_tool(name, args, desc, self._make_tool_handler(mname))
            except Exception as e:
                self._log("agent：注册工具 %s 失败 %r" % (name, e))
        self._log("agent：注册 %d 个工具（send_tools=%s）" % (len(specs), self._agent_cfg().get("send_tools")))
        # AstrBot 的 spec_to_func 只生成 properties、不带 required —— 模型会以为参数可省，
        # 于是出现 send_message({}) 这种空调用。这里手动补上 required。
        need = {"send_message": ["text"], "send_sticker": ["sticker_id"],
                "collect_sticker": ["message_id"], "get_recent_messages": []}
        mgr = None
        for getter in ("get_llm_tool_manager",):
            try:
                mgr = getattr(ctx, getter)()
                break
            except Exception:
                mgr = None
        if mgr is None:
            try:
                mgr = ctx.provider_manager.llm_tools
            except Exception:
                mgr = None
        if mgr is None:
            self._log("agent：拿不到工具管理器，required 未标注（模型可能传空参数）")
            return
        for tname, req in need.items():
            if not req:
                continue
            try:
                ft = mgr.get_func(tname)
                if ft is None:
                    self._log("agent：%s 没找到（required 未标注）" % tname)
                    continue
                params = getattr(ft, "parameters", None)
                if isinstance(params, dict):
                    params.setdefault("properties", {})
                    params["required"] = list(req)
                    self._log("agent：%s 必填参数已标注 %s" % (tname, req))
                else:
                    self._log("agent：%s 的 parameters 不是 dict，无法标注" % tname)
            except Exception as e:
                self._log("agent：%s required 标注失败 %r" % (tname, e))

    def _make_tool_handler(self, mname: str):
        async def _handler(event=None, context=None, **kwargs):
            try:
                t = self._agent_tools(event)
                _cap = int(self._agent_cfg().get("max_calls", 2) or 0)
                if _cap and int(getattr(t, "calls", 0) or 0) >= _cap:
                    self._log("agent工具 %s：本轮次数已用完（上限 %d）" % (mname, _cap))
                    return ("本轮工具次数用完了（最多 %d 次）：别再调工具了，"
                            "直接照你自己的想法说话（发言用正文 / send_message）或者安静结束。" % _cap)
                try:
                    t.calls = int(getattr(t, "calls", 0) or 0) + 1
                except Exception:
                    pass
                fn = getattr(t, mname)
                kw = {k: v for k, v in (kwargs or {}).items() if v is not None}
                out = fn(**kw)
                _o = str(out)
                if _o.startswith(("没发：", "调用失败")) and not kw:
                    # 参数没传全 → 这次不算数，让她重试（否则次数一用完就哑了）
                    try:
                        t.calls = max(0, int(getattr(t, "calls", 0) or 0) - 1)
                    except Exception:
                        pass
                    _o += "（请带上参数重新调用一次：把内容放进对应参数里）"
                self._log("agent工具 %s(%s) → %s" % (mname, str(kwargs)[:100], _o[:70]))
                return _o
            except Exception as e:
                self._log("agent工具 %s 出错: %r" % (mname, e))
                return "调用失败：%r" % (e,)
        return _handler

    def _agent_callbacks(self, target) -> dict:
        ev = (target or {}).get("event")
        _gid = str((target or {}).get("gid") or "")
        _uid = str((target or {}).get("uid") or "")
        _umo = str((target or {}).get("umo") or "")
        try:
            _loop = asyncio.get_running_loop()
        except Exception:
            _loop = None

        def _spawn(coro):
            """agent loop 的工具体是在子线程里跑的，投递协程要用线程安全的方式。"""
            if _loop is not None and _loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, _loop)
            else:
                asyncio.create_task(coro)

        def _send_text(text, reply_to_id="", at_user_id="", face=""):
            _spawn(self._agent_send(target, text, reply_to_id, at_user_id, face))

        def _send_sticker(sid, mood="", reply_to_id=""):
            """发一张表情：给了 id 就发那张；没给或 id 不存在就按 mood 自己挑一张。"""
            sid, mood = str(sid or "").strip(), str(mood or "").strip()
            it = None
            if sid:
                try:
                    it = stickers.find(sid)
                except Exception:
                    it = None
            if not it:
                cand = []
                try:
                    if mood or sid:
                        cand = stickers.pick(mood or sid, limit=6)
                except Exception:
                    cand = []
                if not cand:                     # 兜底：最久没用过的几张里挑
                    try:
                        cand = sorted([x for x in (stickers.load() or []) if x.get("id")],
                                      key=lambda x: int(x.get("last_used") or 0))[:8]
                    except Exception:
                        cand = []
                if cand:
                    _w = [1.0 / (i + 1) for i in range(len(cand))]
                    it = random.choices(cand, weights=_w, k=1)[0]
                    self._log("agent：表情按%s挑中 %s《%s》"
                              % ("情绪「%s」" % mood if mood else "兜底", it.get("id"),
                                 str(it.get("desc") or "")[:20]))
            if not it:
                return False
            _spawn(self._agent_send_sticker(target, it, reply_to_id))
            return True

        def _collect(message_id, note):
            path = ""
            try:
                _ck = _gid
                for _k, _v in list(getattr(self, "_img_cache", {}).items()):
                    if _k[0] == _ck and (str(_k[1]) == str(message_id) or not str(message_id)):
                        if time.time() - _v[0] <= 900 and _v[1]:
                            path = _v[1][0]
                            break
            except Exception:
                path = ""
            if not path:
                try:
                    comps = ([m for m in ev.get_messages() if isinstance(m, Image)]
                             if ev is not None else [])
                except Exception:
                    comps = []
                if not comps:
                    return "这条里没有图片可收藏（只能收刚发过的图）"
                path = self._save_tmp_image(comps[0])
                if not path:
                    return "没收藏：图取不下来"
            try:
                if not path:
                    return "没收藏：图取不下来"
                g = stickers.tag_image(path, str((self.cfg.get("stickers") or {}).get(
                    "vision_model") or "glm-4v-flash"))
                it = stickers.add_file(path, _gid, str(note or g.get("desc") or ""),
                                       g.get("tags") or [], "", int((self.cfg.get("stickers") or {}).get(
                                           "max_store", 300) or 300), g)
                if not it:
                    return "这张要么重复、要么没存进去"
                try:
                    if stickers.add_face(path):
                        it["face_pushed"] = True
                except Exception:
                    pass
                return "收藏好了：%s（%s）" % (it.get("id"), it.get("desc") or note or "")
            except Exception as e:
                return "没收藏：%r" % (e,)

        def _recent(limit):
            out = []
            try:
                for item in list(self.recent.get(_gid) or [])[-limit:]:
                    who = item[0] or "?"
                    out.append("%s：%s" % ("我" if who == "我" else who, (item[1] or "")[:60]))
            except Exception:
                pass
            return out

        def _members():
            try:
                seen, rows = [], []
                for item in reversed(list(self.recent.get(_gid) or [])):
                    who = item[0] or ""
                    if who and who != "我" and who not in seen:
                        seen.append(who)
                    if len(seen) >= 20:
                        break
                for w in seen:
                    rows.append(w)
                return rows
            except Exception:
                return []

        def _memory(query):
            try:
                return self._memory_block(_gid, _uid, self.last_reply_to.get(_gid, ""),
                                          str(query or (target or {}).get("text") or ""))
            except Exception:
                return ""

        def _list_stickers(query):
            q = str(query or "").strip().lower()
            rows = []
            try:
                for it in (stickers.load() or []):
                    blob = ("%s %s %s" % (it.get("desc") or "", " ".join(it.get("tags") or []),
                                          it.get("id") or "")).lower()
                    if q and q not in blob:
                        continue
                    rows.append("- %s ｜ %s ｜ %s" % (it.get("id"), it.get("desc") or "（无备注）",
                                                     "/".join(it.get("tags") or []) or "-"))
                    if len(rows) >= 12:
                        break
            except Exception:
                pass
            return rows

        def _view_image():
            comps = ([m for m in ev.get_messages() if isinstance(m, Image)]
                     if ev is not None else [])
            if not comps:
                return ""
            paths = []
            for c in comps[:2]:
                try:
                    pth = self._save_tmp_image(c)
                    if pth:
                        paths.append(pth)
                except Exception:
                    pass
            if not paths:
                return ""
            txt = agent.vision.describe(paths, self.cfg)
            return ("图里大概是这样：%s" % txt) if txt else ""

        def _send_poke(target=""):
            uid = str(target or _uid or "")
            gid = "" if _gid.startswith("p:") else _gid
            if not uid or not uid.isdigit():
                return False

            async def _do():
                last = None
                # ① 组件方式（最通用）：直接把 Poke 组件发出去
                if Poke is not None:
                    try:
                        await self._transport_send(target, MessageChain([Poke(id=int(uid))]))
                        self._log("agent：拍了一下（Poke 组件 → %s）" % uid)
                        return True
                    except Exception as e:
                        last = e
                        self._log("agent：Poke 组件失败 %r" % (e,))
                # ② 兜底：找适配器上的 call_action
                shapes = []
                if gid and gid != "0":
                    shapes.append({"user_id": int(uid), "group_id": int(gid)})
                shapes.append({"user_id": int(uid)})
                for holder in (getattr(ev, "bot", None), getattr(ev, "api", None),
                               getattr(ev, "_bot", None)):
                    ca = getattr(holder, "call_action", None)
                    if ca is None:
                        continue
                    for act in ("send_poke", "friend_poke", "group_poke", "send_friend_poke"):
                        for prm in shapes:
                            try:
                                _r = ca(act, **prm)
                                if hasattr(_r, "__await__"):
                                    _r = await _r
                                if isinstance(_r, dict) and _r.get("status") not in (None, "ok", "async"):
                                    last = RuntimeError("%s -> %s" % (act, str(_r)[:80]))
                                    continue
                                self._log("agent：拍了一下（%s %s）" % (act, prm))
                                return True
                            except Exception as e:
                                last = e
                self._log("agent：拍失败 %r" % (last,))
                self._fault("poke_fail", repr(last))
                return False
            try:
                if _loop is not None and _loop.is_running():
                    return bool(asyncio.run_coroutine_threadsafe(_do(), _loop).result(timeout=8))
                return bool(asyncio.get_event_loop().run_until_complete(_do()))
            except Exception as e:
                self._log("agent：拍异常 %r" % (e,))
                return False

        def _send_face(name=""):
            fid = self._face_id_by_name(name)
            if not fid or Face is None:
                return False

            async def _do():
                try:
                    await self._transport_send(target, MessageChain([Face(id=fid)]))
                    self._log("agent：发了个 QQ 表情 #%d「%s」" % (fid, self._qq_face_name(fid)))
                    return True
                except Exception as e:
                    self._log("agent：发 QQ 表情失败 %r" % (e,))
                    return False
            try:
                if _loop is not None and _loop.is_running():
                    return bool(asyncio.run_coroutine_threadsafe(_do(), _loop).result(timeout=8))
                return bool(asyncio.get_event_loop().run_until_complete(_do()))
            except Exception as e:
                self._log("agent：发 QQ 表情异常 %r" % (e,))
                return False

        def _view_sticker(sid):
            it = stickers.find(str(sid))
            if not it:
                return ""
            pth = stickers.ensure_local(it)
            if not pth or not os.path.isfile(pth):
                return ""
            return agent.vision.describe([pth], self.cfg, "这张表情图画的是什么？一句话说清（≤30字）。", 160)

        def _sticker_note(sid, note):
            return stickers.set_note(str(sid), str(note))

        def _use_skill(name):
            """技能说明书按需展开（正文直接当工具结果给模型）。"""
            try:
                txt = promptlib.skills.load(PLUGIN_DIR, str(name or "").strip())
            except Exception as e:
                self._fault("skill_fail", repr(e))
                return ""
            if not txt:
                return ""
            self._log("agent：展开技能 %s（%d 字）" % (name, len(txt)))
            return "【技能说明书：%s】\n%s" % (name, txt)

        def _web_search(q, count=5):
            _scfg = self.cfg.get("search") or {}
            if not _scfg.get("enabled", True):
                return "没查：这个会话现在没开联网搜索"
            try:
                _lim = int(_scfg.get("max_per_hour", 20) or 20)
                _ts = getattr(self, "_search_ts", None)
                if _ts is None:
                    _ts = self._search_ts = {}
                _now = time.time()
                _hits = _ts.setdefault(_gid, [])
                _hits[:] = [x for x in _hits if _now - x <= 3600]
                if len(_hits) >= _lim:
                    return ("这小时已经查了 %d 次了，先别查了：直接说你知道的，"
                            "或者老实说不确定" % _lim)
                _hits.append(_now)
            except Exception:
                pass
            try:
                _txt = agent.websearch.search_text(q, count, _scfg)
            except Exception as e:
                self._fault("search_fail", repr(e))
                return "查不了：%r" % (e,)
            if str(_txt).startswith("搜不了"):
                self._fault("search_fail", str(_txt)[:80])
            return _txt

        def _read_url(u):
            _scfg = self.cfg.get("search") or {}
            if not _scfg.get("enabled", True):
                return "没读：这个会话现在没开联网"
            try:
                txt = agent.websearch.read_text(u, _scfg)
            except Exception as e:
                self._fault("search_fail", repr(e))
                return "读不了：%r" % (e,)
            if str(txt).startswith("读不了"):
                self._fault("search_fail", str(txt)[:80])
            return txt

        def _cancel_wake(scope=""):
            try:
                items = self._wake_load()
                keep = [x for x in items if str(x.get("gid") or "") != _gid]
                n = len(items) - len(keep)
                self._wake_save(keep)
                return n
            except Exception:
                return 0

        def _schedule_wake(sec, say, reason, mode="auto", every=0):
            try:
                item = {"umo": _umo,
                        "due": time.time() + int(sec), "say": str(say)[:200],
                        "reason": str(reason or "")[:60], "gid": _gid,
                        "mode": str(mode or "say"), "every": int(every or 0),
                        "poke_uid": _uid}
                items = self._wake_load()
                if len(items) >= 50:
                    items = items[-40:]
                items.append(item)
                self._wake_save(items)
                self._ensure_wake_timer()
                self._log("定时唤醒：%d 秒后（mode=%s%s）→「%s」（%s）"
                          % (int(sec), item["mode"],
                             "，每 %d 秒一次" % item["every"] if item["every"] else "",
                             str(say or reason)[:30], reason))
                if item["mode"] == "think":
                    return "定好了：%d 秒后我自己想一句发出来" % int(sec)
                return "定好了：%d 秒后我会发「%s」%s" % (
                    int(sec), str(say)[:30],
                    "，之后每 %d 秒一次" % item["every"] if item["every"] else "")
            except Exception as e:
                return "定不了：%r" % (e,)

        def _memory_append(kind, text):
            try:
                d = os.path.join(PLUGIN_DIR, "data", "agent_memory")
                os.makedirs(d, exist_ok=True)
                fn = os.path.join(d, (_gid.replace(":", "_") or "x") + ".jsonl")
                with open(fn, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"ts": int(time.time()), "kind": str(kind or "impression")[:16],
                                         "text": str(text or "")[:120]}, ensure_ascii=False) + "\n")
                return "记下了"
            except Exception as e:
                return "没记住：%r" % (e,)

        cbs = {"recent_lines": _recent, "active_members": _members,
               "memory_lookup": _memory, "list_stickers": _list_stickers,
               "view_image": _view_image, "send_poke": _send_poke,
               "send_face": _send_face,
               "view_sticker": _view_sticker, "sticker_note": _sticker_note,
               "schedule_wake": _schedule_wake, "cancel_wake": _cancel_wake,
               "memory_append": _memory_append}
        try:                       # 允许发表情/工具？按会话判断（event 不在时也能算，定时轮用得上）
            _ac = self._agent_cfg()
            _gs = [str(x) for x in (_ac.get("send_tools_groups") or [])]
            _send_ok = bool(_ac.get("send_tools")) and ((not _gs) or (_gid in _gs))
        except Exception:
            _send_ok = False
        if _send_ok:
            # 只有名单内的群/私聊才给发送类工具；否则她照旧用正文说话
            cbs.update({"send_text": _send_text, "send_sticker": _send_sticker,
                        "collect_sticker": _collect, "send_ok": True})
        return cbs

    @staticmethod
    def _extra(event, key, default=None):
        """读 event 上的附加标记（测试桩里没有 get_extra 也能用）。"""
        try:
            return event.get_extra(key, default)
        except Exception:
            return default

    def _fault(self, name: str, detail: str = "") -> None:
        """故障计数：内存 + 落盘 data/agent_faults.json（20 秒最多写一次）。"""
        try:
            if not hasattr(self, "_faults"):
                self._faults = {}
                self._faults_ts = 0.0
            self._faults[name] = int(self._faults.get(name, 0)) + 1
            self._log("⚠️ 故障 %s=%d %s" % (name, self._faults[name], detail[:80]))
            now = time.time()
            if now - getattr(self, "_faults_ts", 0) > 20:
                self._faults_ts = now
                _d = os.path.join(PLUGIN_DIR, "data")
                os.makedirs(_d, exist_ok=True)
                with open(os.path.join(_d, "agent_faults.json"), "w", encoding="utf-8") as f:
                    json.dump({"updated": int(now), "counts": self._faults}, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def _agent_target(self, event=None, target=None, **kw) -> dict:
        t = dict(target or {})
        if event is not None:
            t.setdefault("event", event)
            t.setdefault("umo", str(getattr(event, "unified_msg_origin", "") or ""))
            t.setdefault("gid", self._chat_key(event))
            try:
                t.setdefault("uid", str(event.get_sender_id() or ""))
            except Exception:
                pass
            try:
                t.setdefault("text", str(getattr(event, "message_str", "") or ""))
            except Exception:
                pass
        for k, v in kw.items():
            if v is not None:
                t[k] = v
        return t

    def _agent_tools(self, event):
        """取/建"这次唤醒"的工具会话（工具绑定当前会话，模型无法指定发到别处）。"""
        key = str(getattr(event, "unified_msg_origin", "") or self._chat_key(event))
        if not hasattr(self, "_agent_sessions"):
            self._agent_sessions = {}
        t = self._agent_sessions.get(key)
        ttl = float(self._agent_cfg().get("session_ttl", 300) or 300)
        if t is None or (time.time() - getattr(t, "started", 0) > ttl):
            if len(self._agent_sessions) > 200:
                self._agent_sessions.clear()
            t = agent.tools.Tools(self._chat_key(event), self._agent_callbacks(self._agent_target(event)),
                                  self._agent_cfg())
            self._agent_sessions[key] = t
        return t

    async def _transport_send(self, target, chain) -> bool:
        """统一发送口：有 event 走 event.send；定时（无 event）走 context.send_message。"""
        try:
            _ev = (target or {}).get("event")
            if _ev is not None:
                await _ev.send(chain)
                return True
            _umo2 = str((target or {}).get("umo") or "")
            _ctx = getattr(self, "context", None)
            if _umo2 and _ctx is not None and hasattr(_ctx, "send_message"):
                return bool(await _ctx.send_message(_umo2, chain))
            self._log("agent：没有可用发送通道")
            return False
        except Exception as e:
            self._log("agent：发送失败 %r" % (e,))
            return False

    async def _agent_send(self, target, text: str, reply_to_id: str = "",
                          at_user_id: str = "", face: str = "") -> None:
        try:
            comps = []
            if reply_to_id:
                comps.append(Reply(id=str(reply_to_id)))
            if at_user_id:
                comps.append(At(qq=str(at_user_id)))
            # 文字里如果夹着 [表情:id]（图片表情），抽出来单独发一条，别当文字发出去
            _imgs = []
            try:
                import re as _re2
                for _m2 in _re2.finditer(r"\[表情(?::([^\]\n]{1,24}))?\]", str(text or "")):
                    _imgs.append((_m2.group(1) or "").strip())
                text = _re2.sub(r"\[表情(?::[^\]\n]{1,24})?\]", "", str(text or "")).strip()
            except Exception:
                _imgs = []
            # 抽完图片表情再统一清洗（[QQ表情:…] 保留，那是要变成真表情的）
            try:
                _clean = replyproto.sanitize(text, strip_bar=True)
                if _clean != str(text or ""):
                    self._log_debug("发送口兜底：清洗 %r → %r" % (str(text or "")[:40], _clean[:40]))
                text = _clean
            except Exception:
                pass
            _faces = []
            try:
                for _kind, _val in replyproto.split_faces(text):
                    if _kind == "text":
                        if _val.strip():
                            comps.append(Plain(_val))
                    else:
                        _fid = self._face_id_by_name(_val)
                        if _fid and Face is not None:
                            comps.append(Face(id=_fid))
                            _faces.append(_val)
                        else:
                            _faces.append("?" + _val)
            except Exception:
                comps.append(Plain(str(text)))
            for _sid in _imgs[:2]:
                try:
                    _it = None
                    for _cand in (stickers.load() or []):
                        if _sid and str(_cand.get("id") or "") == _sid:
                            _it = _cand
                            break
                    if _it is None and _sid:
                        _it = stickers.find(_sid)
                    if _it:
                        asyncio.create_task(self._agent_send_sticker(self._agent_target(event), _it))
                        self._log("agent：图片表情 %s 单独发一条" % _it.get("id"))
                except Exception as _e3:
                    self._log_debug("抽图片表情失败 %r" % (_e3,))
            _tail = self._face_id_by_name(face) if face else 0
            if _tail and Face is not None:
                try:
                    comps.append(Face(id=_tail))
                    _faces.append(face)
                except Exception:
                    pass
            if not comps:
                self._log("发送口兜底：内容全是内部标记，这条不发")
                self._fault("marker_only_text")
                return
            await self._transport_send(target, MessageChain(comps))
            self._log("agent：发出「%s」%s" % (str(text)[:40],
                                              ("＋表情%s" % "、".join(_faces)) if _faces else ""))
            try:                                   # 自己说过的也记进"最近群聊"（供上下文 + 防重复）
                _rk = str((target or {}).get("gid") or "")
                if _rk.startswith("p:"):
                    _rk = ""
                if _rk not in [str(x) for x in (self.cfg.get("no_context_groups") or [])]:
                    _buf = self.recent.get(_rk)
                    if _buf is None:
                        _buf = deque(maxlen=max(2, int(self.cfg.get("context_lines", 6))))
                        self.recent[_rk] = _buf
                    _buf.append(("我", str(text)[:60], ""))
            except Exception:
                pass
        except Exception as e:
            self._log("agent：发送失败 %r" % (e,))

    async def _agent_send_sticker(self, target, it: dict, reply_to_id: str = "") -> None:
        try:
            path = await asyncio.to_thread(stickers.ensure_local, it)
            if not path or not os.path.exists(path):
                self._log("agent：表情没落盘，不发 %s" % it.get("id"))
                return
            try:
                if hasattr(Image, "fromFileSystem"):
                    img = Image.fromFileSystem(path)
                else:
                    img = Image(file=path)
            except Exception:
                img = Image(file=path)
            comps = [Reply(id=str(reply_to_id))] if reply_to_id else []
            comps.append(img)
            await self._transport_send(target, MessageChain(comps))
            stickers.mark_used(it.get("id"))
            self._log("agent：发出表情 %s《%s》" % (it.get("id"), it.get("desc")))
        except Exception as e:
            self._log("agent：发表情失败 %r" % (e,))

    def _agent_tools_text(self, key: str = "") -> str:
        """给提示词用的工具清单（只在 agent 打开时给；给了 key 就按会话裁剪）。"""
        try:
            specs = self._agent_specs_for(key) if key else self._agent_tool_specs()
            if not specs:
                return ""
            return "\n".join("- %s：%s" % (n, d) for n, _a, d, _m in specs)
        except Exception:
            return ""

    def _agent_loop_on(self, event) -> bool:
        c = self._agent_cfg()
        if not c.get("loop_mode"):
            return False
        groups = [str(x) for x in (c.get("loop_groups") or [])]
        return (not groups) or (self._chat_key(event) in groups)

    def _qq_face_name(self, fid: int) -> str:
        """QQ 自带表情的官方名字（SnowLuma 的 sys-face-catalog.json，qSid → qDes）。"""
        try:
            m = getattr(self, "_face_map", None)
            if m is None:
                m = {}
                for _p in ("/opt/snowluma/data/sys-face-catalog.json",
                           os.path.join(PLUGIN_DIR, "data", "sys-face-catalog.json")):
                    try:
                        _d = json.load(open(_p, encoding="utf-8"))
                    except Exception:
                        continue
                    for _pk in (_d.get("packs") or []):
                        for _e in (_pk.get("emojis") or []):
                            _sid = str(_e.get("qSid") or "").strip()
                            _des = str(_e.get("qDes") or "").strip().lstrip("/")
                            if _sid.isdigit() and _des and _sid not in m:
                                m[_sid] = _des
                    if m:
                        break
                self._face_map = m
                self._log("agent：载入 QQ 自带表情名 %d 条" % len(m))
            return m.get(str(fid), "")
        except Exception:
            return ""

    def _face_id_by_name(self, name: str) -> int:
        """官方名字 / 数字 id → QQ 表情 id。"""
        t = str(name or "").strip()
        if not t:
            return 0
        if t.isdigit():
            return int(t)
        t = t.lstrip("/").strip()
        try:
            self._qq_face_name(-1)              # 确保表已载入
            for k, v in (getattr(self, "_face_map", {}) or {}).items():
                if v == t:
                    return int(k)
        except Exception:
            pass
        return 0

    def _face_menu_text(self) -> str:
        """给她一份"能发的 QQ 自带表情"清单（官方名字=id）。"""
        try:
            self._qq_face_name(-1)
            m = getattr(self, "_face_map", {}) or {}
            pop = [4, 14, 5, 6, 9, 13, 16, 20, 21, 25, 27, 28, 30, 32, 38, 39, 43,
                   53, 61, 63, 66, 69, 71, 72, 74, 77, 79, 96, 97, 98, 277, 307, 319, 324, 354, 426]
            got = [(m.get(str(i)) or "", i) for i in pop if m.get(str(i))]
            if not got:
                return ""
            return ("【可以发的 QQ 自带表情（用 send_face，参数写名字）】\n"
                    + "、".join("%s=%d" % (n, i) for n, i in got))
        except Exception:
            return ""

    def _wake_path(self) -> str:
        return os.path.join(PLUGIN_DIR, "data", "agent_wake.json")

    def _wake_load(self) -> list:
        try:
            with open(self._wake_path(), encoding="utf-8") as f:
                return list(json.load(f) or [])
        except Exception:
            return []

    def _wake_save(self, items: list) -> None:
        try:
            os.makedirs(os.path.dirname(self._wake_path()), exist_ok=True)
            tmp = self._wake_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(items[-100:], f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._wake_path())
        except Exception as e:
            self._log("定时唤醒：写盘失败 %r" % (e,))

    def _ensure_wake_timer(self) -> None:
        """起一个后台任务，到点把话说出来（用 context.send_message 主动发）。"""
        try:
            if getattr(self, "_wake_task", None) is not None and not self._wake_task.done():
                return
            self._wake_task = asyncio.get_event_loop().create_task(self._wake_loop())
            self._log("定时唤醒：后台计时任务已启动")
        except Exception as e:
            self._log_debug("定时唤醒：计时任务启动失败 %r" % (e,))

    async def _wake_loop(self) -> None:
        while True:
            try:
                now = time.time()
                items = self._wake_load()
                due = [x for x in items if float(x.get("due") or 0) <= now]
                keep = [x for x in items if float(x.get("due") or 0) > now
                        and now - float(x.get("due") or 0) < 86400]
                if due:
                    self._wake_save(keep)
                for it in due[:5]:
                    await self._wake_fire(it)
                # 动态睡眠：睡到下一个到期时刻（上限 20 秒），这样 5 秒的短延时也能准时
                try:
                    _nxt = min([float(x.get("due") or 0) for x in keep])
                except Exception:
                    _nxt = 0.0
                _wait = 20.0 if not _nxt else max(1.0, min(20.0, _nxt - time.time() + 0.2))
                await asyncio.sleep(_wait)
            except asyncio.CancelledError:
                return
            except Exception as e:
                self._log("定时唤醒循环出错(忽略): %r" % (e,))
                await asyncio.sleep(5)

    async def _agent_proactive(self, it: dict, reason: str) -> None:
        """定时到点的"主动轮"：没有新消息进来，她自己决定说什么/做什么（可用全部工具）。"""
        gid = str(it.get("gid") or "")
        target = self._agent_target(target={"gid": gid, "umo": str(it.get("umo") or ""),
                                            "uid": str(it.get("poke_uid") or ""),
                                            "text": reason or ""})
        private = gid.startswith("p:")
        _kcfg = self.cfg.get("kb") or {}
        _txt, _meta = promptlib.build_system_prompt(
            plugin_dir=PLUGIN_DIR, cfg=self.cfg, chat_key="" if private else gid, private=private,
            caps={"vision": True, "search": self._search_on(),
                  "kb": bool(_kcfg.get("enabled", True) and not private),
                  "tools_text": self._agent_tools_text(gid), "tools_send": True,
                  "self_id": str(self.cfg.get("allowed_self_id") or ""),
                  "aliases": list(self.cfg.get("keywords") or [])})
        _menu = self._sticker_menu_text("private" if private else gid)
        if _menu:
            _txt += "\n\n" + _menu
        _fmenu = self._face_menu_text()
        if _fmenu:
            _txt += "\n\n" + _fmenu
        t = agent.tools.Tools(gid, self._agent_callbacks(target), self._agent_cfg())
        schema = agent.loop.spec_to_openai(self._agent_specs_for(gid))
        lines = []
        try:
            for item in list(self.recent.get(gid) or [])[-8:]:
                lines.append("%s：%s" % (item[0] or "?", (item[1] or "")[:60]))
        except Exception:
            pass
        user = ""
        if lines:
            user += "[最近群聊]\n" + "\n".join(lines) + "\n"
        user += ("[主动机会] 现在是你自己之前定的时间点（%s）。没有人刚叫你，是你自己想说话："
                 "想说什么就调 send_message，想发表情/拍一拍也行；不想说就调 finish。"
                 "别解释定时、别提系统、别汇报。") % (reason or "随便看看")
        self._log("定时唤醒：主动轮开始（%s，reason=%s）" % (gid, (reason or "")[:20]))
        r = await asyncio.to_thread(agent.loop.run, t,
                                    [{"role": "system", "content": _txt},
                                     {"role": "user", "content": user}],
                                    schema, None, int(self._agent_cfg().get("max_rounds", 2) or 2),
                                    self._log)
        self._log("定时唤醒：主动轮结束（说了 %d 条，finish=%s，%s）"
                  % (len(t.sent), t.finished, str((r or {}).get("usage") or {})))

    async def _wake_fire(self, it: dict) -> None:
        try:
            umo = str(it.get("umo") or "")
            ctx = getattr(self, "context", None)
            if not umo or ctx is None or not hasattr(ctx, "send_message"):
                self._log("定时唤醒：没有会话或发送通道，跳过")
                return
            raw = str(it.get("say") or "").strip()
            _reason = str(it.get("reason") or "").strip()
            # 循环任务：先把下一次排进去（先续期，避免中途失败丢了循环）
            try:
                _every = int(it.get("every") or 0)
                if _every >= 60:
                    items = self._wake_load()
                    nxt = dict(it)
                    nxt["due"] = time.time() + _every
                    items.append(nxt)
                    self._wake_save(items)
                    self._log("定时唤醒：循环任务已续期（%d 秒后）" % _every)
            except Exception as _e4:
                self._log_debug("定时唤醒：续期失败 %r" % (_e4,))

            # think 模式：先让她自己组织一句（调用一次模型，不带工具）
            if str(it.get("mode") or "say") == "think":
                # 主动轮：她可以用全部工具（说话/表情/拍一拍/查记录/再定下一次…）
                try:
                    await self._agent_proactive(it, _reason or raw)
                except Exception as _e5:
                    self._log("定时唤醒：主动轮失败 %r" % (_e5,))
                    self._fault("proactive_fail", repr(_e5))
                return
            if not raw:
                self._log("定时唤醒：没有内容，跳过")
                return

            # ① 拍一拍：[拍一拍] / [poke]
            if "[拍一拍]" in raw or "[poke]" in raw.lower():
                uid = str(it.get("poke_uid") or "")
                if uid and Poke is not None:
                    ok = bool(await ctx.send_message(umo, MessageChain([Poke(id=int(uid))])))
                    self._log("定时唤醒：拍了 %s → %s" % (uid, ok))
                else:
                    self._log("定时唤醒：拍不出去（缺 uid 或 Poke 组件不可用）")
                return

            # ② 图片表情：[表情:id]
            import re as _re3
            _m = _re3.search(r"\[表情(?::([^\]\n]{1,24}))?\]", raw)
            if _m:
                _sid = (_m.group(1) or "").strip()
                _it2 = stickers.find(_sid) if _sid else None
                if _it2 is None:
                    try:
                        _cands = stickers.load() or []
                        _it2 = random.choice(_cands) if _cands else None
                    except Exception:
                        _it2 = None
                if _it2:
                    try:
                        _p = stickers.ensure_local(_it2)
                        if _p and os.path.isfile(_p):
                            try:
                                _img = (Image.fromFileSystem(_p) if hasattr(Image, "fromFileSystem")
                                        else Image(file=_p))
                            except Exception:
                                _img = Image(file=_p)
                            ok = bool(await ctx.send_message(umo, MessageChain([_img])))
                            stickers.mark_used(_it2.get("id"))
                            self._log("定时唤醒：发出图片表情 %s → %s" % (_it2.get("id"), ok))
                        else:
                            self._log("定时唤醒：表情图没落盘，发不出")
                    except Exception as _e2:
                        self._log("定时唤醒：发图片表情失败 %r" % (_e2,))
                else:
                    self._log("定时唤醒：找不到表情（id=%s）" % _sid)
                raw = _re3.sub(r"\[表情(?::[^\]\n]{1,24})?\]", "", raw).strip()
                if not raw:
                    return

            # ③ 文字（支持内联 [QQ表情:…]）
            txt = replyproto.sanitize(raw, strip_bar=True)
            if not txt:
                return
            comps = []
            for _kind, _val in replyproto.split_faces(txt):
                if _kind == "text":
                    if _val.strip():
                        comps.append(Plain(_val))
                else:
                    _fid = self._face_id_by_name(_val)
                    if _fid and Face is not None:
                        comps.append(Face(id=_fid))
            if not comps:
                return
            ok = bool(await ctx.send_message(umo, MessageChain(comps)))
            self._log("定时唤醒：发出「%s」→ %s" % (txt[:30], ok))
        except Exception as e:
            self._log("定时唤醒：发送失败 %r" % (e,))

    def _agent_lock(self, key: str):
        if not hasattr(self, "_agent_locks"):
            self._agent_locks = {}
        lk = self._agent_locks.get(key)
        if lk is None:
            if len(self._agent_locks) > 200:
                self._agent_locks.clear()
            lk = asyncio.Lock()
            self._agent_locks[key] = lk
        return lk

    async def _agent_loop_run(self, event) -> bool:
        """同一个会话串行跑 loop：连发时排队，等上一轮说完再开下一轮。"""
        key = self._chat_key(event)
        lk = self._agent_lock(key)
        if lk.locked():
            self._log("agent loop：%s 上一轮还没说完，这条排队等" % key)
        try:
            _to = float(self._agent_cfg().get("queue_timeout", 45) or 45)
        except Exception:
            _to = 45.0
        try:
            await asyncio.wait_for(lk.acquire(), timeout=_to)
        except asyncio.TimeoutError:
            self._log("agent loop：%s 排队超时（%.0fs），交回原流程" % (key, _to))
            return False
        try:
            return await self._agent_loop_body(event)
        finally:
            lk.release()

    def _agent_specs_for(self, key: str) -> list:
        """按会话过滤工具清单：不发表情图的群（send/collect_exclude_groups）滤掉表情图工具。"""
        specs = self._agent_tool_specs()
        # 按会话裁剪工具（省前缀 token）：tool_profile_by_group 指到哪个档，就只留那一档的工具
        try:
            _acfg = self._agent_cfg()
            _prof = (_acfg.get("tool_profile_by_group") or {}).get(str(key))
            if _prof:
                _allow = (_acfg.get("tool_profiles") or {}).get(str(_prof))
                if _allow:
                    _keep = {str(x) for x in _allow}
                    specs = [x for x in specs if x[0] in _keep]
                    self._log_debug("agent：%s 用工具档 %s（%d 个）" % (key, _prof, len(specs)))
        except Exception:
            pass
        try:
            _scfg = self.cfg.get("stickers") or {}
            _no_stk = (key in [str(x) for x in (_scfg.get("send_exclude_groups") or [])]
                       or key in [str(x) for x in (_scfg.get("collect_exclude_groups") or [])])
            if _no_stk:
                specs = [x for x in specs if x[0] not in ("send_sticker", "collect_sticker")]
                self._log_debug("agent loop：%s 不发表情图 → 滤掉 send_sticker/collect_sticker" % key)
        except Exception:
            pass
        return specs

    async def _agent_loop_body(self, event) -> bool:
        """自己驱动这一轮：tool_choice=required 强制她调工具说话。

        返回 True 表示"这轮我们处理了"（调用方负责 stop_event）；False 表示交回原流程。
        """
        key = self._chat_key(event)
        private = key.startswith("p:")
        _kcfg = self.cfg.get("kb") or {}
        _txt, _meta = promptlib.build_system_prompt(
            plugin_dir=PLUGIN_DIR, cfg=self.cfg, chat_key=key, private=private,
            caps={"vision": True, "search": self._search_on(),
                  "kb": bool(_kcfg.get("enabled", True) and not private),
                  "tools_text": self._agent_tools_text(key), "tools_send": True,
                  "self_id": str(event.get_self_id() or ""),
                  "aliases": (list(self.cfg.get("keywords") or []) + ([self._nick] if self._nick else []))})
        _menu = self._sticker_menu_text("private" if private else key)
        system_prompt = _txt + (("\n\n" + _menu) if _menu else "")
        _fmenu = self._face_menu_text()
        if _fmenu:
            system_prompt += "\n\n" + _fmenu
        # 每一轮都用**全新**的工具会话：否则上一轮的 sent/finished 会累积，
        # 既让日志出现"说了 5 条"，还会让陈旧 spoke 把这一轮的多轮工具调用掐掉
        _akey = str(getattr(event, "unified_msg_origin", "") or self._chat_key(event))
        t = agent.tools.Tools(self._chat_key(event), self._agent_callbacks(self._agent_target(event)),
                              self._agent_cfg())
        try:
            if len(self._agent_sessions) > 200:
                self._agent_sessions.clear()
            self._agent_sessions[_akey] = t
        except Exception:
            pass
        self._ensure_wake_timer()          # 定时唤醒的计时任务（懒启动）
        schema = agent.loop.spec_to_openai(self._agent_specs_for(key))
        if not schema:
            self._log("agent loop：没有可用工具，交回原流程")
            return False
        lines, _mine = [], []
        try:
            _rk = "" if key.startswith("p:") else str(event.get_group_id() or "")
            _recent = list(self.recent.get(_rk) or [])
            for item in _recent[-10:]:
                lines.append("%s：%s" % (item[0] or "?", (item[1] or "")[:60]))
            for item in _recent[-8:]:
                if str(item[0] or "") == "我" and str(item[1] or "").strip():
                    _mine.append(str(item[1])[:30])
        except Exception:
            pass
        user = ""
        if lines:
            user += "[最近群聊（'我'=你自己说的，只作参考）]\n" + "\n".join(lines) + "\n"
        if _mine:
            user += ("[你最近说过的几句（尽量换个说法、别老用同一句；语境确实需要重复时可以重复，"
                     "但别连着重复同一句）]\n" + "\n".join("- " + m for m in _mine[-5:]) + "\n")
        user += "[当前消息] " + str(getattr(event, "message_str", "") or "")
        try:
            if _face_note:
                user += " " + _face_note
        except Exception:
            pass
        # 消息里带了 unicode 表情（🥺😭👍…）：给她中文名，免得她猜
        try:
            _UNI = {"🥺": "可怜", "😊": "微笑", "😌": "得意", "😂": "笑哭", "🤣": "笑哭",
                    "😭": "大哭", "👍": "赞", "👎": "踩", "❤️": "爱心", "🐶": "狗头",
                    "🙏": "抱拳", "😅": "冷汗", "😎": "酷", "😴": "睡", "🤔": "疑问",
                    "😳": "害羞", "😡": "发怒", "🤡": "小丑", "😍": "色", "🥰": "亲亲"}
            _hits = [(e, n) for e, n in _UNI.items() if e in str(getattr(event, "message_str", "") or "")]
            if _hits:
                user += "（他消息里的表情：" + "、".join("%s=%s" % (e, n) for e, n in _hits) + "）"
        except Exception:
            pass
        # 软提醒：让她想起"可以配表情"（概率性、有冷却，避免每条都念叨）
        try:
            _nd = self._sticker_nudge(key)
            if _nd:
                user += "\n" + _nd
        except Exception:
            pass
        # 她自己写的记忆：最近几条注入到提示词末尾
        try:
            _mf = os.path.join(PLUGIN_DIR, "data", "agent_memory",
                               (key.replace(":", "_") or "x") + ".jsonl")
            if os.path.isfile(_mf):
                _notes = []
                with open(_mf, encoding="utf-8") as _fh:
                    for _line in _fh.readlines()[-8:]:
                        try:
                            _o = json.loads(_line)
                            _notes.append("- [%s] %s" % (_o.get("kind"), _o.get("text")))
                        except Exception:
                            pass
                if _notes:
                    system_prompt += "\n\n【你自己之前记下的（仅供参考，别硬提）】\n" + "\n".join(_notes)
        except Exception:
            pass
        # 本地资料库：按当前话题检索后注入（loop 路径以前没有这一段，等于资料白建）
        try:
            _kcfg = self.cfg.get("kb") or {}
            if _kcfg.get("enabled", True) and not private:
                _q = " ".join([str(getattr(event, "message_str", "") or "")] +
                              [x[1] for x in list(self.recent.get("" if key.startswith("p:") else key) or [])[-3:]])
                _hits = local_kb.search(_q, int(_kcfg.get("top_k", 3) or 3),
                                        float(_kcfg.get("min_score", 0.08) or 0.08))
                if _hits:
                    _buf, _cap, _used = [], int(_kcfg.get("max_chars", 900) or 900), 0
                    for _h in _hits:
                        _t = "【%s】%s" % (_h.get("source"), _h.get("text"))
                        if _used + len(_t) > _cap:
                            break
                        _buf.append(_t)
                        _used += len(_t)
                    if _buf:
                        system_prompt += ("\n\n【本地资料库（可能和当前话题有关；自然的时候用上，"
                                          "别照抄、别提\"资料库\"三个字）】\n" + "\n\n".join(_buf))
                        self._log("loop 资料库：注入 %d 块（%s）"
                                  % (len(_buf), "、".join(str(_h.get("source")) for _h in _hits[:len(_buf)])))
        except Exception as _ek:
            self._log_debug("loop 资料库注入失败 %r" % (_ek,))
        # 记忆块（群印象/人物档案）也带进 loop 的提示词
        try:
            _mb = self._memory_block(key.split(":")[-1] if key.startswith("p:") else key,
                                     str(event.get_sender_id() or ""),
                                     self.last_reply_to.get(key, ""), user)
            if _mb and _mb not in system_prompt:
                system_prompt += "\n\n" + _mb
        except Exception:
            pass
        # 消息里有图 → 走"看图轮"：多模态直接回答（deepseek-flash 带 tools 会 400）
        _img_paths = []
        try:
            _comps = list(event.get_messages() or [])
            try:
                _comps += list(getattr(getattr(event, "message_obj", None), "message", None) or [])
            except Exception:
                pass
            _names = [type(x).__name__ for x in _comps]
            _txt_now = str(getattr(event, "message_str", "") or "").strip()
            if _names and not _txt_now:
                self._log("agent loop：消息组件=%s" % _names)
            _face_hint = ""
            for _m in _comps:
                _cn = type(_m).__name__
                if _cn == "Face" or str(getattr(_m, "type", "") or "").lower() == "face":
                    try:
                        _fid = int(getattr(_m, "id", 0) or 0)
                    except Exception:
                        _fid = 0
                    if _fid:
                        _nm = self._qq_face_name(_fid)
                        _face_hint = (_face_hint + ("、" if _face_hint else "")
                                      + ("「%s」#%d" % (_nm, _fid) if _nm else "#%d" % _fid))
                    continue
                _is_img = (isinstance(_m, Image)
                           or type(_m).__name__ in ("Image", "Picture")
                           or str(getattr(_m, "type", "") or "").lower() == "image")
                if not _is_img:
                    continue
                _p = self._save_tmp_image(_m)
                if _p:
                    _img_paths.append(_p)
                else:
                    self._log("agent loop：有图但取不下来（组件=%s）" % _names)
            if _face_hint:
                _face_note = ("[对方发来的是 QQ 自带表情 %s（这是官方表情名，按这个名字的含义自然接话；"
                              "你看不到它的图案，不要猜图案、不要编画面）]" % _face_hint)
            else:
                _face_note = ""
        except Exception as _e:
            self._log("agent loop：取图异常 %r" % (_e,))
        if _img_paths:
            _parts = []
            for _p in _img_paths[:2]:
                try:
                    _b64, _ext = "", (os.path.splitext(_p)[1].lstrip(".").lower() or "jpeg")
                    try:
                        import io as _io
                        from PIL import Image as _PIL
                        _im = _PIL.open(_p)
                        _w, _h = _im.size
                        if max(_w, _h) < 560:              # 小图放大再送：手势/文字才看得清
                            _sc = 2
                            if min(_w, _h) and min(_w, _h) < 60:      # QQ 表情只有 24px → 放大到 ~200px
                                _sc = max(2, int(200 / min(_w, _h)) + 1)
                            _im2 = _im.convert("RGB").resize((_w * _sc, _h * _sc), _PIL.LANCZOS)
                            _buf = _io.BytesIO()
                            _im2.save(_buf, format="JPEG", quality=92)
                            _b64 = base64.b64encode(_buf.getvalue()).decode()
                            _ext = "jpeg"
                            self._log("agent loop：小图放大 %dx%d → %dx%d 再识别"
                                      % (_w, _h, _w * _sc, _h * _sc))
                    except Exception:
                        pass
                    if not _b64:
                        _b64 = base64.b64encode(open(_p, "rb").read()).decode()
                    _parts.append({"type": "image_url",
                                   "image_url": {"url": "data:image/%s;base64,%s" % (_ext, _b64)}})
                except Exception:
                    pass
            # 这张图如果她自己收藏过，把备注/标签一起给她（比纯看图准得多）
            _known = ""
            try:
                _h = stickers.ahash(_img_paths[0])
                for _it in (stickers.load() or []):
                    if str(_it.get("hash") or "") == str(_h):
                        _known = ("%s %s" % (str(_it.get("desc") or ""),
                                             " ".join(_it.get("tags") or []))).strip()
                        break
            except Exception:
                _known = ""
            if _parts:
                _n_img = len([x for x in _parts if x.get("type") == "image_url"])
                _parts.append({"type": "text", "text": user})
                _hint = ("【提示】这张图你自己的表情库里有记录：%s（可参考，但以你看到的为准）\n" % _known[:60]) if _known else ""
                _parts.append({"type": "text", "text": (
                    _hint +
                    "看清这张图再说话，**先客观描述、再谈意思**：\n"
                    "① 图上有文字就**照抄原文**（一字不改，别意译）；\n"
                    "② 手势/动作只写客观事实：「拇指向上」「手指朝前/朝某人」「握拳」「挥手」「比心」「没有手」；\n"
                    "③ 严禁把朝向或姿势脑补成剧情（手指朝前 ≠ 冲过来、≠ 攻击你）；看不清就写「看不清手势」，**不要猜**。\n"
                    "然后**只输出三行**（不要别的解释）：\n"
                    "图：<主体 + 图上文字原文 + 客观手势 + 大致情绪，≤40字；这行会被你记住>\n"
                    "回：<你现在要对他说的话，最多 2 条，多条用 ||| 分隔>\n"
                    "收：<是 或 否 —— 这张图以后想不想当表情包用？觉得有意思/用得上就写 是>")})
                _vmodel = agent.vision.vision_model(self.cfg)
                try:
                    _mid = str(getattr(getattr(event, "message_obj", None), "message_id", "") or "")
                    _ck = self._chat_key(event)
                    if _mid and _img_paths:
                        if len(self._img_cache) > 60:
                            self._img_cache.clear()
                        self._img_cache[(_ck, _mid)] = (time.time(), list(_img_paths))
                    self._log("agent loop：看图轮（%d 张图 → %s，不用工具）" % (_n_img, _vmodel))
                except Exception:
                    pass
                _vtxt = ""
                try:
                    _vtxt = await asyncio.to_thread(
                        agent.llm.chat_vision,
                        [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": _parts}], _vmodel, self.cfg, 500)
                except Exception as _e:
                    self._log("agent loop：看图轮失败 %r" % (_e,))
                    self._fault("vision_fail", repr(_e))
                _vtxt = str(_vtxt or "").strip()

                def _after(_line):
                    for _sep in ("：", ":"):
                        if _sep in _line:
                            return _line.split(_sep, 1)[1].strip()
                    return _line

                _desc, _say, _keep = "", "", False
                for _line in _vtxt.splitlines():
                    _l = _line.strip()
                    if not _l:
                        continue
                    if _l.startswith("图") and not _desc:
                        _desc = _after(_l)
                    elif _l.startswith(("回", "回复", "答", "说")) and not _say:
                        _say = _after(_l)
                    elif _l.startswith("收"):
                        _keep = _after(_l).strip().lower().startswith(("是", "y", "收", "1", "true", "要"))
                if not _say:
                    # 没给【回】行：剥掉协议行（图/收/回），剩下的正文才当回复；只剩协议行就干脆不回
                    _rest = []
                    for _line in _vtxt.splitlines():
                        _l2 = _line.strip()
                        if not _l2 or _l2.startswith(("图", "收", "回", "回复")):
                            continue
                        _rest.append(_l2)
                    _say = " ".join(_rest).strip()
                    if _say:
                        self._log("agent loop：看图轮没给【回】行，用剩余正文兜底 %d 字" % len(_say))
                    else:
                        self._log("agent loop：看图轮只给了协议行 → 不回（不把思考发出去）")
                if _desc:
                    # ① 写进"最近群聊"：后面几轮她都看得见
                    try:
                        _buf = self.recent.get(str(event.get_group_id() or ""))
                        if _buf is not None:
                            _buf.append(("<图片>", _desc, ""))
                    except Exception:
                        pass
                    # ② 写进长期记忆：跨轮、跨天都在（每轮注入）
                    try:
                        _d = os.path.join(PLUGIN_DIR, "data", "agent_memory")
                        os.makedirs(_d, exist_ok=True)
                        _mf = os.path.join(_d, (self._chat_key(event).replace(":", "_") or "x") + ".jsonl")
                        with open(_mf, "a", encoding="utf-8") as _fh:
                            _fh.write(json.dumps({"ts": int(time.time()), "kind": "image",
                                                  "text": "他发过一张图：%s" % _desc},
                                                 ensure_ascii=False) + "\n")
                        self._log("agent loop：图片已记住 —— %s" % _desc[:40])
                    except Exception as _e:
                        self._log("agent loop：图片记忆写入失败 %r" % (_e,))
                if _keep and _desc and _img_paths:
                    try:
                        _sc = self.cfg.get("stickers") or {}
                        _key = self._chat_key(event)
                        _lim = int((_sc.get("private_max_per_hour", 50) if _key.startswith("p:")
                                    else _sc.get("collect_max_per_hour", 10)) or 10)
                        _now = time.time()
                        _hist = getattr(self, "sticker_collect_ts", None)
                        if _hist is None:
                            _hist = self.sticker_collect_ts = {}
                        _hits = _hist.setdefault(_key, [])
                        _hits[:] = [x for x in _hits if _now - x <= 3600]
                        if len(_hits) >= _lim:
                            self._log("看图轮：这个小时收藏够了，先不收")
                        else:
                            _it = stickers.add_file(_img_paths[0], _key, _desc, [], "",
                                                    int(_sc.get("max_store", 300) or 300), None)
                            if _it:
                                _hits.append(_now)
                                self._log("看图轮：收下表情 %s《%s》（她觉得有意思）"
                                          % (_it.get("id"), _desc[:24]))
                                try:
                                    if stickers.add_face(_img_paths[0]):
                                        _it["face_pushed"] = True
                                        self._log("看图轮：已同步进 QQ 表情面板")
                                except Exception as _e2:
                                    self._log_debug("看图轮：同步面板失败 %r" % (_e2,))
                            else:
                                self._log("看图轮：想收但没入库（可能重复）")
                    except Exception as _e:
                        self._log("看图轮：收藏出错 %r" % (_e,))
                if _say:
                    _mk = str((self.cfg.get("split_reply") or {}).get("marker", "|||"))
                    _segs = replyproto.parse(_say, _mk, 4).parts or [_say]
                    for _seg in _segs[:4]:
                        await self._agent_send(self._agent_target(event), _seg)
                    self._log("agent loop：看图轮发出 %d 条" % len(_segs[:4]))
                else:
                    self._log("agent loop：看图轮没拿到内容")
                try:
                    event.set_extra("_agent_loop_done", True)
                except Exception:
                    pass
                return True
        # 空消息（没文字、也没取到图）：多半是 QQ 自带表情/大表情或其他系统消息，
        # 现在读不出内容 → 静默跳过，别回"怎么了"这种莫名其妙的话。
        if (not str(getattr(event, "message_str", "") or "").strip() and not _img_paths
                and not _face_hint):
            try:
                _raw = getattr(event, "raw_message", None)
                if _raw is None:
                    _mo = getattr(event, "message_obj", None)
                    _raw = getattr(_mo, "raw_message", None) or getattr(_mo, "message", None)
                self._log("agent loop：空消息，先不回。raw=%r" % (str(_raw)[:200],))
            except Exception:
                pass
            try:
                event.stop_event()
            except Exception:
                pass
            return True

        _vmodel = None
        _user_content = (user + (" " + _face_note if _face_hint else ""))
        if False:
            _parts = []
            for _p in _img_paths[:2]:
                try:
                    _b64 = base64.b64encode(open(_p, "rb").read()).decode()
                    _ext = os.path.splitext(_p)[1].lstrip(".").lower() or "jpeg"
                    _parts.append({"type": "image_url",
                                   "image_url": {"url": "data:image/%s;base64,%s" % (_ext, _b64)}})
                except Exception:
                    pass
            if _parts:
                _parts.append({"type": "text", "text": user})
                _user_content = _parts
                _vmodel = agent.vision.vision_model(self.cfg)
                self._log("agent loop：带 %d 张图 → 用 %s 直接看图" % (len(_parts) - 1, _vmodel))
        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user", "content": _user_content}]
        try:
            _rounds = int(self._agent_cfg().get("max_rounds", 2) or 2)
        except Exception:
            _rounds = 2
        self._log("agent loop：开始（%s，%d 个工具，system %d 字，最多 %d 轮）"
                  % (key, len(schema), len(system_prompt), _rounds))
        r = await asyncio.to_thread(agent.loop.run, t, messages, schema, None, _rounds,
                                    self._log_debug, _vmodel)
        self._log("agent loop：结束（%s，用了 %s，说了 %d 条，finish=%s）"
                  % (key, str(r.get("usage") or {}), len(t.sent), t.finished))
        # 硬提醒：对方明确要求"X 秒后/过一会儿再说/再做"，但她没调 schedule_wake
        try:
            _cur = str(getattr(event, "message_str", "") or "")
            _called = {str(c.get("name") or "") for c in (r.get("calls") or [])}
            if replyproto.wants_schedule(_cur) and "schedule_wake" not in _called:
                if not getattr(self, "_wake_nagged", None):
                    self._wake_nagged = set()
                _nk = (key, int(time.time() // 30))
                if _nk not in self._wake_nagged:
                    self._wake_nagged.add(_nk)
                    if len(self._wake_nagged) > 200:
                        self._wake_nagged.clear()
                    self._log("agent loop：对方要求定时但没调 schedule_wake → 提醒重试一轮")
                    messages.append({"role": "user", "content": (
                        "【系统提醒】对方要求你「过一会儿 / X 秒后再做或再说」，"
                        "你必须调用 schedule_wake(after_sec=秒数, say=到时要说的话) 把它定上；"
                        "不要现在就把话说出来，也不要只用嘴答应。刚才若已经回过话，这轮只调 schedule_wake。")})
                    r2 = await asyncio.to_thread(agent.loop.run, t, messages, schema, None, 1,
                                                 self._log_debug, _vmodel)
                    self._log("agent loop：定时提醒后 %s" % (
                        "成功" if t.sent or t.finished else "仍没成功"))
                    r = r2 if r2 else r
        except Exception as _e:
            self._log_debug("定时提醒重试失败 %r" % (_e,))
        if not t.spoke and not t.finished:
            # 她既没说也没结束：再提醒一轮（传统渠道已退休，这里就是最后的安全网）
            try:
                messages.append({"role": "user", "content": (
                    "【系统提醒】你刚才没有发言。要说话就调 send_message（多条用 ||| 分隔）；"
                    "不想说话就调 finish。别把话写在正文里，正文不会发出去。")})
                r2 = await asyncio.to_thread(agent.loop.run, t, messages, schema, None, 1,
                                             self._log, _vmodel)
                self._log("agent loop：漏发提醒后 %s" % ("补上了" if t.spoke else "仍未发言"))
                r = r2 if r2 else r
            except Exception as _e6:
                self._log_debug("漏发提醒失败 %r" % (_e6,))
        if not t.spoke and not t.finished:
            self._log("agent loop：这轮没发言也没 finish → 视为沉默")
            self._fault("no_speak")
        try:
            event.set_extra("_agent_loop_done", True)
        except Exception:
            pass
        return True

    def _sticker_nudge(self, key: str = "") -> str:
        """软提醒：闲聊里顺手配一张表情。不强制、不刷屏（同一会话 8 分钟内最多提一次）。"""
        try:
            cfg = self.cfg.get("stickers") or {}
            if not cfg.get("enabled", True):
                return ""
            key = str(key or "")
            if key.startswith("p:"):
                if not cfg.get("send_private", True):
                    return ""
            elif key and key in [str(x) for x in (cfg.get("send_exclude_groups") or [])]:
                return ""
            p = float(cfg.get("nudge_prob", 0.35) or 0)
            if p <= 0:
                return ""
            ts = getattr(self, "_nudge_ts", None)
            if ts is None:
                ts = self._nudge_ts = {}
            now = time.time()
            if now - float(ts.get(key) or 0) < 480:
                return ""
            if random.random() > p:
                return ""
            ts[key] = now
            return ("【顺手】这轮要是自然（接梗/吐槽/被逗笑/附和/道晚安/别人发了表情），"
                    "可以顺手配一张表情：send_sticker(mood=「想说的一句」) 让她自己挑，"
                    "或者 send_sticker(sticker_id=「清单里的 id」) 指名。不合适就纯文字，别硬塞。")
        except Exception:
            return ""

    def _sticker_menu_text(self, gid: str = "") -> str:
        """【可用表情包】清单：让模型用 [表情:id] 指名发图。

        稳定性：按用过次数排序取一半固定，另一半按小时轮换（同一小时内内容不变 →
        前缀缓存友好，且不会总发同几张）。
        """
        try:
            cfg = self.cfg.get("stickers") or {}
            if not cfg.get("enabled", True):
                return ""
            if gid == "private":
                if not cfg.get("send_private", True):
                    return ""
            elif gid and gid in [str(x) for x in (cfg.get("send_exclude_groups") or [])]:
                return ""
            n = int(cfg.get("prompt_max", 8) or 0)
            if n <= 0:
                return ""
            items = [x for x in (stickers.load() or []) if str(x.get("id") or "")]
            if not items:
                return ""
            items.sort(key=lambda x: (-int(x.get("used") or 0), str(x.get("id"))))
            n = max(1, min(int(n), len(items)))
            stable_n = max(1, n // 2)
            stable = items[:stable_n]
            pool = list(items[stable_n:])
            if len(pool) > 1:
                random.Random(int(time.time() // 3600)).shuffle(pool)
            picked = stable + pool[:n - stable_n]
            lines = []
            for it in picked:
                desc = str(it.get("desc") or "").strip() or "（还没备注这图什么意思）"
                tags = "/".join([str(t) for t in (it.get("tags") or [])][:4])
                lines.append("- %s ｜ %s%s" % (it.get("id"), desc,
                                               ("［%s］" % tags) if tags else ""))
            return ("【可用表情包】她的收藏里共 %d 张，下面是常用的和她这小时轮到的 %d 张：\n%s\n"
                    "要发就调 send_sticker：指名哪张 → send_sticker(sticker_id=「上面的 id」)；"
                    "只想配个情绪/场景 → send_sticker(mood=「无语」或「笑死」或「点赞」或「晚安」)，"
                    "她自己从收藏里挑一张贴切的。表情单独一条消息发，别和文字挤一起。"
                    % (len(items), len(picked), "\n".join(lines)))
        except Exception as e:
            self._log_debug("表情清单拼装失败: %r" % (e,))
            return ""

    def _protocol_parse(self, event):
        """按输出协议解析她这条回复：分条 / 沉默 / 表情标记。"""
        try:
            sc = self.cfg.get("split_reply") or {}
            result = event.get_result()
            txt = ""
            for c in (getattr(result, "chain", None) or []):
                if isinstance(c, Plain):
                    txt += c.text or ""
            cap = int((sc.get("char_mode") or {}).get("max_parts", 24))
            return replyproto.parse(txt, str(sc.get("marker", "|||")), cap,
                                    allow_lines=True, silence=bool(sc.get("silence", True)))
        except Exception as e:
            self._log_debug("协议解析失败: %r" % (e,))
            return replyproto.Parsed(parts=[])

    def _followup_cfg(self) -> dict:
        c = dict(self.cfg.get("followup") or {})
        c.setdefault("enabled", True)
        c.setdefault("chance", 0.12)
        c.setdefault("max_per_hour", 2)
        c.setdefault("delay_ms", 2600)
        c.setdefault("exclude_groups", ["966812151"])
        return c

    def _maybe_followup(self, event: AstrMessageEvent, multi: bool = False) -> None:
        """偶尔像真人一样，接着自己刚发的那条再补一句。只做概率判断，不花 token。

        multi=True：她这条已经按输出协议自己分了多条，就不再额外补话（免得话密）。
        """
        try:
            if multi:
                return
            c = self._followup_cfg()
            if not c.get("enabled"):
                return
            gid = self._chat_key(event)
            if not gid or self._in("no_context_groups", gid):
                return
            if gid in [str(x) for x in (c.get("exclude_groups") or [])]:
                return
            try:                                  # 玩海龟汤的时候别插嘴补话
                if (turtle_session.load(gid) or {}).get("active"):
                    return
            except Exception:
                pass
            result = event.get_result()
            txt = ""
            for x in (getattr(result, "chain", None) or []):
                if isinstance(x, Plain):
                    txt += x.text or ""
            txt = " ".join(txt.split())
            if len(txt) < 6 or len(txt) > 300:
                return
            if random.random() > float(c.get("chance", 0.12) or 0):
                return
            now = time.time()
            hits = self.__dict__.setdefault("_followup_hits", {}).setdefault(gid, [])
            hits[:] = [x for x in hits if now - x <= 3600]
            if len(hits) >= int(c.get("max_per_hour", 2) or 2):
                self._log_debug("followup: 这个小时补够了")
                return
            hits.append(now)
            asyncio.create_task(self._do_followup(event, txt, c))
        except Exception as e:
            self._log("followup 调度失败(忽略): %r" % (e,))

    async def _do_followup(self, event: AstrMessageEvent, prev: str, c: dict) -> None:
        try:
            base = float(c.get("delay_ms", 2600) or 2600) / 1000.0
            await asyncio.sleep(max(0.6, base * random.uniform(0.7, 1.4)))
            note = await asyncio.to_thread(followup.make_note, prev)
            if not note:
                return
            await event.send(MessageChain([Plain(note)]))
            self._log("followup: 接着自己补一句 —— %s" % note)
            try:
                _k = str(event.get_group_id() or "")
                buf = self.recent.get(_k)
                if buf is not None:
                    buf.append(("我", note, ""))
            except Exception:
                pass
        except Exception as e:
            self._log("followup 发送失败(忽略): %r" % (e,))

    def _quote_reason(self, event: AstrMessageEvent) -> str:
        """什么时候像真人一样用引用：只有"我回的这条已经被后来的消息顶上去了"才引用。"""
        gid = str(event.get_group_id() or "")
        mid = str(getattr(getattr(event, "message_obj", None), "message_id", "") or "")
        if mid and self.cfg.get("quote_when_stale", True):
            last = self.last_msg.get(gid)
            if last and last[0] and last[0] != mid:
                return "newer"                       # 中间又来了新消息 → 引用，避免答非所问
        if self.cfg.get("quote_when_replied", False):
            try:
                for m in event.get_messages():
                    if isinstance(m, Reply):
                        return "thread"              # 对方本来就是引用着某条消息说的
            except Exception:
                pass
        return ""

    @filter.on_decorating_result()
    async def smart_quote(self, event: AstrMessageEvent) -> None:
        """发送前最后一步：只在需要指明"回哪条"时才加引用（全局 reply_with_quote 已关）。"""
        try:
            # agent 工具：她已经用工具说过话（或明确结束本轮）→ 正文只是思考，不发出去
            if self._extra(event, "_agent_loop_done", False):
                _akey = str(getattr(event, "unified_msg_origin", "") or self._chat_key(event))
                _at = self._agent_sessions.pop(_akey, None)
                _wrote = ""
                try:
                    for _c in (getattr(event.get_result(), "chain", None) or []):
                        if isinstance(_c, Plain):
                            _wrote += str(_c.text or "")
                except Exception:
                    pass

                def _drop():
                    try:
                        event.get_result().chain = []
                    except Exception:
                        pass

                if _at is not None and _at.spoke:
                    _drop()
                    self._log("agent：本轮已用工具发言，正文不发（%s）" % (_at.state(),))
                    return
                if not self._agent_cfg().get("rescue_text", False):
                    _drop()
                    if _wrote.strip():
                        self._log("agent：漏发！写了正文但没调 send_message（%d 字，正文不发）：%s"
                                  % (len(_wrote), _wrote[:40]))
                        self._fault("dropped_text", _wrote[:40])
                    else:
                        self._log("agent：本轮没发言也没调 finish → 视为沉默")
                    return
            # 输出协议：整条只输出 [不说话] → 这条不发（也不记入"我说过的话"）
            pr = self._protocol_parse(event)
            if pr.silent and (self.cfg.get("split_reply") or {}).get("silence", True):
                try:
                    event.get_result().chain = []
                except Exception:
                    pass
                self._log("协议：[不说话] → 这条不发（%s）" % self._chat_key(event))
                return
            # 兜底路径同样过滤内部协议行（||| 保留给分条用）
            try:
                for _c in (getattr(event.get_result(), "chain", None) or []):
                    if isinstance(_c, Plain) and str(_c.text or "").strip():
                        _c.text = replyproto.sanitize(_c.text, strip_bar=False, strip_stickers=False)
            except Exception:
                pass
            self._remember_reply(event)
            await self._maybe_sticker(event)
            self._maybe_followup(event, multi=pr.multi)   # 已经自己分了多条就不再补话
            if not self.cfg.get("smart_quote", True):
                return
            gid = str(event.get_group_id() or "")
            if not gid or gid == "0":
                return                               # 私聊不引用
            result = event.get_result()
            chain = list(getattr(result, "chain", None) or [])
            if not chain or isinstance(chain[0], Reply):
                return
            if not all(isinstance(x, (Plain, Image)) for x in chain):
                return                               # 只有纯文本/图片才适合加引用
            # ---- 像真人一样，把长回复拆成几条发（纯发送侧，不多花 token）----
            try:
                sc = self.cfg.get("split_reply") or {}
                excl = [str(x) for x in (sc.get("exclude_groups") or [])]
                if sc.get("enabled") and str(gid) not in excl:
                    chain_now = list(getattr(result, "chain", None) or [])
                    plains = [c for c in chain_now if isinstance(c, Plain)]
                    if (len(plains) == 1
                            and not any(isinstance(c, (Image,)) for c in chain_now)
                            and not any(isinstance(c, Reply) for c in chain_now[1:])):   # 引用只挂第一条
                        full = plains[0].text or ""
                        # 分段优先级（新）：模型输出协议 > 逐字关键词 > 兜底按长度
                        cap_parts = int((sc.get("char_mode") or {}).get("max_parts", 24))
                        char_mode = bool((getattr(self, "_char_mode", {}) or {}).pop(
                            getattr(event, "unified_msg_origin", ""), False))
                        cm = sc.get("char_mode") or {}
                        ps = list(pr.parts) if pr.parts else []
                        src = {"marker": "模型标记", "lines": "模型换行"}.get(pr.source, "")
                        extra_delay, extra_jitter = None, 0.3
                        if len(ps) <= 1 and char_mode and cm.get("enabled", True):
                            # 关键词触发"逐字"（模型没自己分段时）＋ 每小时上限保护
                            try:
                                cap_h = int(cm.get("max_per_hour", 60))
                            except Exception:
                                cap_h = 60
                            hour_key = (str(gid), int(time.time() // 3600))
                            used_h = (getattr(self, "_char_hour", {}) or {}).get(hour_key, 0)
                            if used_h + len(full) > cap_h:
                                self._log("逐字模式已达每小时上限(%d)，本条改为普通拆条" % cap_h)
                            else:
                                ps = self._char_parts(full, int(cm.get("max_parts", 24)))
                                extra_delay = float(cm.get("delay_ms", 800)) / 1000.0
                                extra_jitter = float(cm.get("jitter", 0.3))
                                if not hasattr(self, "_char_hour"):
                                    self._char_hour = {}
                                if len(self._char_hour) > 48:
                                    self._char_hour.clear()
                                self._char_hour[hour_key] = used_h + len(ps)
                                src = "逐字"
                        if len(ps) <= 1 and sc.get("heuristic_fallback", True):
                            # 模型没自己分条时的兜底：按标点/长度切，且绝不切在英文单词中间
                            ps = self._split_text(full, int(sc.get("max_parts", 5)),
                                                  int(sc.get("min_chars", 12)))
                            src = "兜底切分" if len(ps) > 1 else "单条"
                        if len(ps) > 1:
                            plains[0].text = ps[0]
                            result.chain = chain_now
                            self._spawn_parts(event, ps[1:], delay=extra_delay, jitter=extra_jitter)
                            self._log("拆成 %d 条发[%s]：%s" % (len(ps), src,
                                                            " ｜ ".join(p[:14] for p in ps)))
            except Exception as e:
                self._log("拆条发送失败(忽略): %r" % (e,))

            reason = self._quote_reason(event)
            self._log_debug("QUOTE hook: mid=%s last=%s reason=%r" % (getattr(getattr(event,"message_obj",None),"message_id",None), self.last_msg.get(gid), reason))
            if not reason:
                return
            mid = getattr(getattr(event, "message_obj", None), "message_id", None)
            if mid:
                chain.insert(0, Reply(id=str(mid)))
                result.chain = chain
                self._log("smart_quote: 加引用(%s) id=%s" % (reason, mid))
        except Exception as e:
            import traceback
            self._log("quote hook ERROR: %r | %s" % (e, traceback.format_exc()[-300:].replace("\n", " ")))

    @filter.on_waiting_llm_request()
    async def image_fastpath(self, event: AstrMessageEvent) -> None:
        """图片/表情包快速通道：在 AstrBot 下载、压缩、转 base64 之前就把图片组件摘掉。

        当前对话模型（deepseek-chat）本来就不支持视觉输入，图片只会白跑一遍
        「下载 → 压缩 → base64 → 请求被拒/被忽略」，纯浪费时间和带宽。
        这里换成一行文字提示，她照样能就着气氛接话，但整条链路都不跑了。
        """
        try:
            msgs = list(event.get_messages() or [])
            if not msgs or not any(isinstance(x, Image) for x in msgs):
                return
            if not str(event.get_group_id() or ""):
                self._spawn_sticker_collect(event)     # 私聊发图也收
            if not self.cfg.get("image_fastpath", True):
                return
            kept = []
            n_img = 0
            for x in msgs:
                if isinstance(x, Image):
                    n_img += 1
                    kept.append(Plain("[图片]"))
                else:
                    kept.append(x)
            try:
                event.message_obj.message = kept
            except Exception:
                pass
            try:
                self._img_flag[event.unified_msg_origin] = True
                self._img_hint[event.unified_msg_origin] = True
            except Exception:
                pass
            self._log("image_fastpath: 摘掉 %d 个图片组件（不下载/不压缩/不传图）" % n_img)
        except Exception as e:
            self._log("image_fastpath ERROR: %r" % (e,))

    @filter.on_llm_request()
    async def compact_context(self, event: AstrMessageEvent, req) -> None:
        """无状态化：丢掉历史上下文，只注入最近几条群聊摘要（模拟"已读/未读"）。"""
        try:
            try:
                t_in = self._t_in.get(event.unified_msg_origin, 0.0)
                if t_in:
                    self._log("耗时: 收消息→发出请求 %.2fs（图片 %d 张，人格=%s）"
                              % (time.time() - t_in,
                                 len(getattr(req, "image_urls", None) or []),
                                 os.path.basename(self._prompt_path(str(event.get_group_id() or "")))))
            except Exception:
                pass
            gid = str(event.get_group_id() or "")
            private = (not gid or gid == "0")
            # 人格覆盖：私聊和群聊都做（私聊走 "private" 档）
            if self.cfg.get("override_system_prompt", True):
                try:
                    pcfg = self.cfg.get("prompt") or {}
                    if pcfg.get("layered", True) is False:
                        pf = self._prompt_path(gid if not private else "private")
                        with open(pf, encoding="utf-8") as f:
                            req.system_prompt = f.read().strip()
                        self._log("提示词(旧单层): %s" % os.path.basename(pf))
                    else:
                        _kcfg = self.cfg.get("kb") or {}
                        _txt, _meta = promptlib.build_system_prompt(
                            plugin_dir=PLUGIN_DIR, cfg=self.cfg,
                            chat_key=(self._chat_key(event) if private else gid), private=private,
                            caps={"vision": bool(self.cfg.get("vision", False)),
                                  "search": bool((self.cfg.get("search") or {}).get("enabled")),
                                  "kb": bool(_kcfg.get("enabled", True) and not private),
                                  "tools_text": self._agent_tools_text(gid),
                                  "tools_send": bool(self._agent_cfg().get("send_tools")),
                                  "self_id": str(event.get_self_id() or ""),
                                  "aliases": (list(self.cfg.get("keywords") or [])
                                              + ([self._nick] if self._nick else []))})
                        req.system_prompt = _txt
                        self._log("提示词: 人格=%s 补丁=%s 行为%d段；人格%d字/行为%d字/共%d字；参与=%s 表情档=%s"
                                  % (_meta["persona"], _meta["patch"], _meta["sections"],
                                     _meta["persona_chars"], _meta["behavior_chars"], _meta["chars"],
                                     _meta["participation"], _meta["sticker_level"]))
                    self._ensure_agent_tools()          # 幂等：确保 agent 工具已注册
                    _menu = self._sticker_menu_text("private" if private else gid)
                    if _menu:
                        req.system_prompt = (req.system_prompt or "") + "\n\n" + _menu
                except Exception as e:
                    self._log("拼装提示词失败: %r" % (e,))
            # ---- 本地资料库：把跟当前话题相关的资料块注入提示词 ----
            try:
                _kcfg = self.cfg.get("kb") or {}
                if _kcfg.get("enabled", True) and not private:
                    _q = " ".join([str(event.message_str or "")] +
                                  [x[1] for x in list(self.recent.get(gid) or [])[-3:]])
                    _hits = local_kb.search(_q, int(_kcfg.get("top_k", 3) or 3),
                                            float(_kcfg.get("min_score", 0.14) or 0.14))
                    if _hits:
                        _buf, _cap, _used = [], int(_kcfg.get("max_chars", 900) or 900), 0
                        for _h in _hits:
                            _t = "【%s】%s" % (_h.get("source"), _h.get("text"))
                            if _used + len(_t) > _cap:
                                break
                            _buf.append(_t)
                            _used += len(_t)
                        if _buf:
                            req.system_prompt = (getattr(req, "system_prompt", "") or "") + \
                                "\n\n【本地资料库（可能和当前话题有关；只在自然的时候用上，别照抄、别提\"资料库\"三个字）】\n" + \
                                "\n\n".join(_buf)
                            self._log("本地资料库：注入 %d 块（%s）"
                                      % (len(_buf), "、".join(str(_h.get("source")) for _h in _hits[:len(_buf)])))
            except Exception as _e:
                self._log_debug("本地资料库注入失败: %r" % (_e,))
            if private:
                try:
                    ft_p = getattr(req, "func_tool", None)
                    if ft_p is not None and getattr(ft_p, "tools", None) \
                            and self.cfg.get("drop_tools_private", True):
                        ft_p.tools = []      # 私聊同样摘掉工具 schema（省 ~900 token/次）
                except Exception:
                    pass
                return                       # 私聊保留上下文：她记得你们聊过什么，不做压缩
            if not self.cfg.get("compact_context", True):
                return
            self._log_debug("hook: contexts=%d prompt_len=%d sys_len=%d" % (
                len(getattr(req, "contexts", None) or []), len(req.prompt or ""), len(getattr(req, "system_prompt", "") or "")))
            no_memes = str(gid or "") in [str(x) for x in (self.cfg.get("no_memes_groups") or [])]
            if self.cfg.get("inject_memes", True) and not no_memes:
                try:
                    mem = self._memes_text()
                    if mem and mem not in (req.system_prompt or ""):
                        req.system_prompt = (req.system_prompt or "") + "\n\n" + mem
                except Exception:
                    pass
            if self.cfg.get("drop_tools", True):
                ft = getattr(req, "func_tool", None)
                if ft is not None and getattr(ft, "tools", None):
                    if self._agent_cfg().get("enabled"):
                        # agent 打开时：只保留我们自己的工具，别的一律摘掉
                        self._ensure_agent_tools()
                        _names = {n for n, _a, _d, _m in self._agent_tool_specs()}
                        ft.tools = [t for t in ft.tools if getattr(t, "name", "") in _names]
                    else:
                        ft.tools = []                 # 群聊不需要工具，省掉工具 schema 的 token
            try:
                parts = getattr(req, "extra_user_content_parts", None) or []
                if parts:
                    self._log_debug("extra_parts: %s" % str(parts)[:200])
            except Exception:
                pass
            try:
                sc_hint = self.cfg.get("split_reply") or {}
                if sc_hint.get("hint", True) and sc_hint.get("hint_text"):
                    if sc_hint["hint_text"] not in (req.system_prompt or ""):
                        req.system_prompt = (req.system_prompt or "") + "\n\n" + sc_hint["hint_text"]
            except Exception:
                pass
            mem_block = self._memory_block(gid, str(event.get_sender_id() or ""),
                                           self.last_reply_to.get(gid, ""),
                                           str(getattr(req, "prompt", "") or event.message_str or ""))
            if mem_block:
                req.system_prompt = (req.system_prompt or "") + "\n\n" + mem_block
            if getattr(req, "contexts", None):
                req.contexts = []                     # 关键：不发送历史
            if self._img_flag.pop(event.unified_msg_origin, False):
                try:
                    req.image_urls = []               # 保险：别把图塞进请求
                    req.extra_user_content_parts = [
                        p for p in (getattr(req, "extra_user_content_parts", None) or [])
                        if getattr(p, "type", "") != "image_url"
                    ]
                except Exception:
                    pass
            buf = None if self._in("no_context_groups", str(gid)) else self.recent.get(gid)
            if buf:
                items = list(buf)
                # 把"当前发送者刚连发的几句"从摘要里拿出来，拼成一整句当前消息（真人就是把一句话拆成几条发的）
                who_now = ""
                try:
                    who_now = str(event.get_sender_name() or event.get_sender_id() or "")
                except Exception:
                    pass
                uid_now = str(event.get_sender_id() or "")
                tail = []
                while items and len(items[-1]) > 2 and items[-1][2] == uid_now:
                    tail.insert(0, items.pop()[1])
                if not tail and who_now:
                    while items and items[-1][0] == who_now:
                        tail.insert(0, items.pop()[1])
                cur = " ".join([x for x in (tail + [req.prompt or ""]) if x]).strip()
                if items:
                    lines = "\n".join("%s: %s" % (it[0], it[1]) for it in items)
                    head = ("[最近群聊（'我'=你自己说过的话；仅供理解语境，不要逐条回应、不要复述、不要重复自己说过的话）]\n"
                            + lines + "\n")
                else:
                    head = ""
                req.prompt = head + "[当前消息] " + (cur or (req.prompt or ""))
            if self.cfg.get("dump_prompt"):
                try:
                    import json as _json
                    d = os.path.join(PLUGIN_DIR, "data")
                    os.makedirs(d, exist_ok=True)
                    with open(os.path.join(d, "prompt-dump.jsonl"), "a", encoding="utf-8") as f:
                        def _s(x):
                            try: return str(x)[:4000]
                            except Exception: return ""
                        f.write(_json.dumps({"t": int(time.time()), "gid": gid,
                                             "sys": req.system_prompt, "prompt": req.prompt,
                                             "ctx": _s(getattr(req, "contexts", None)),
                                             "extra": _s(getattr(req, "extra_user_content_parts", None)),
                                             "imgs": _s(getattr(req, "image_urls", None)),
                                             "tools": _s([getattr(t, "name", str(t)) for t in (getattr(getattr(req, "func_tool", None), "tools", None) or [])]),
                                             "conv": _s(bool(getattr(req, "conversation", None)))},
                                            ensure_ascii=False) + "\n")
                except Exception as e:
                    self._log("dump 失败: %r" % (e,))
            try:
                mc = (getattr(self, "_mirror_char", {}) or {}).pop(event.unified_msg_origin, 0)
                if mc:
                    req.prompt = ("[对方是一个字一个字发过来的（%d 条），他想看你能不能用同样的形式回："
                                  "你也把回复拆成一小块一小块，用换行隔开，想一个字一个字发就一个字一行]\n" % mc
                                  ) + (req.prompt or "")
                    self._log("逐字镜像：告诉模型对方是逐字发的（%d 条）" % mc)
            except Exception:
                pass
            if self._img_hint.pop(event.unified_msg_origin, False):
                req.prompt = (
                    "[对方这次发的是图片/表情包，你看不到画面内容，别装看得见、别描述画面；"
                    "可以让他说说是啥，或者就着重气氛接一句]\n"
                ) + (req.prompt or "")
            self._log_debug("after: sys=%d ctx=%d prompt=%d tools=%s extra=%d" % (
                len(req.system_prompt or ""), len(getattr(req, "contexts", None) or []), len(req.prompt or ""),
                len(getattr(getattr(req, "func_tool", None), "tools", None) or []),
                len(getattr(req, "extra_user_content_parts", None) or [])))
        except Exception as e:
            import traceback
            self._log("hook ERROR: %r | %s" % (e, traceback.format_exc()[-300:].replace("\n", " ")))

    async def _bot_nickname(self, event: AstrMessageEvent) -> str:
        """取机器人当前昵称（缓存 10 分钟）——改名后不用改配置。"""
        now = time.time()
        if self._nick and now - self._nick_ts < 600:
            return self._nick
        try:
            info = await asyncio.wait_for(
                event.bot.call_action("get_login_info", self_id=str(event.get_self_id())), timeout=3
            )
            nick = str((info or {}).get("nickname") or "").strip()
            if nick:
                self._nick, self._nick_ts = nick, now
                return nick
        except Exception as e:
            self._log("get_login_info failed: %r" % (e,))
        return self._nick

    def _quotes_me(self, event: AstrMessageEvent) -> bool:
        """这条消息是不是"引用/回复着她的消息"在说（QQ 引用回复）。

        判定：引用组件里的发送者是她自己，或引用的原文和她最近说过的话高度重合。
        """
        me = str(event.get_self_id())
        my_lines = [x[1] for x in (self.recent.get(str(event.get_group_id() or "")) or []) if str(x[0]) == "我"]
        try:
            for msg in event.get_messages():
                if not isinstance(msg, Reply):
                    continue
                for attr in ("sender_id", "qq"):
                    v = getattr(msg, attr, 0)
                    try:
                        if v and str(v) not in ("0", "None") and str(v) == me:
                            return True
                    except Exception:
                        pass
                quoted = " ".join(str(getattr(msg, a, "") or "") for a in ("message_str", "text"))
                if quoted.strip() and my_lines:
                    if any(scoring._overlap(quoted, line) >= 0.3 or quoted.strip() in line for line in my_lines):
                        return True
        except Exception:
            pass
        return False

    def _at_other(self, event: AstrMessageEvent) -> bool:
        """这条消息是不是在 @ 别人（@全体不算）。"""
        me = str(event.get_self_id())
        try:
            for msg in event.get_messages():
                if isinstance(msg, At) and str(getattr(msg, "qq", "")) not in (me, "all", ""):
                    return True
        except Exception:
            pass
        return False

    def _note_sender(self, gid: str, uid: str, text: str) -> None:
        """记录发送者时间线（识别刷屏）和上一条内容（识别复读）。"""
        if not gid or gid == "0":
            return
        now = time.time()
        buf = self.sender_times.get(gid)
        if buf is None:
            buf = deque(maxlen=60)
            self.sender_times[gid] = buf
        self.prev_speaker[gid] = self.last_speaker.get(gid, "")
        self.last_speaker[gid] = str(uid)
        try:
            if not hasattr(self, "msg_lens"):
                self.msg_lens = {}
            lb = self.msg_lens.get(gid)
            if lb is None:
                lb = deque(maxlen=16)
                self.msg_lens[gid] = lb
            lb.append((now, str(uid), len(" ".join((text or "").split()))))
        except Exception:
            pass
        buf.append((now, str(uid)))
        while buf and now - buf[0][0] > 300:
            buf.popleft()
        if len(self.sender_text) > 3000:
            self.sender_text.clear()
        self.sender_text[(gid, str(uid))] = " ".join((text or "").split())[:120]

    # ---------------- 表情包 ----------------
    @staticmethod
    def _save_tmp_image(comp) -> str:
        """把消息里的图片落到本地临时文件，返回路径（拿不到返回空串）。"""
        import tempfile
        f = str(getattr(comp, "path", "") or getattr(comp, "file", "") or
                getattr(comp, "url", "") or "").strip()
        if not f:
            return ""
        try:
            data = None
            if f.startswith("base64://"):
                data = base64.b64decode(f[9:])
            elif f.startswith("data:"):
                data = base64.b64decode(f.split(",", 1)[1])
            elif f.startswith("file://"):
                from urllib.request import url2pathname
                with open(url2pathname(f[7:]), "rb") as fh:
                    data = fh.read()
            elif f.startswith("http://") or f.startswith("https://"):
                req = urllib.request.Request(f, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = r.read(3 * 1024 * 1024)
            elif os.path.exists(f):
                with open(f, "rb") as fh:
                    data = fh.read()
            if not data:
                return ""
            ext = os.path.splitext(f)[1].lower()
            if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
                ext = ".png"
            fd, path = tempfile.mkstemp(suffix=ext, prefix="stk_")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            return path
        except Exception:
            return ""

    def _spawn_sticker_collect(self, event) -> None:
        """群友发了图 → 后台识图，合格的收进表情包收藏夹。"""
        try:
            cfg = self.cfg.get("stickers") or {}
            if not cfg.get("enabled", True):
                return
            gid = self._chat_key(event)
            private = gid.startswith("p:")
            if private:
                if not cfg.get("collect_private", True):
                    return
            elif gid in [str(x) for x in (cfg.get("collect_exclude_groups") or [])]:
                return
            comps = [m for m in event.get_messages() if isinstance(m, Image)]
            if not comps:
                return
            now = time.time()
            lst = getattr(self, "sticker_collect_ts", None)
            if lst is None:
                lst = self.sticker_collect_ts = {}
            hits = lst.setdefault(gid, [])
            hits[:] = [x for x in hits if now - x <= 3600]
            _limit = int((cfg.get("private_max_per_hour", 50) if private
                          else cfg.get("collect_max_per_hour", 10)) or 10)
            if len(hits) >= _limit:
                return
            hits.append(now)

            async def _run():
                for c in comps[:2]:
                    path = await asyncio.to_thread(self._save_tmp_image, c)
                    if not path:
                        continue
                    try:
                        g = await asyncio.to_thread(stickers.tag_image, path,
                                                    str(cfg.get("vision_model") or "glm-4v-flash"))
                        _kind = str(g.get("kind") or "").lower()
                        _worth = g.get("worth")      # 识图自己判的"这图有没有趣、值不值得收"
                        _hard_bad = _kind in ("qr", "ad")     # 二维码/广告：一律不收
                        if (not private) and _kind == "screenshot":
                            keep = False                     # 群里：屏幕截图一律不收（游戏/软件/网页/聊天记录）
                        elif _worth is not None:
                            # 其余以"她看过觉得有没有梗"为准（真人梗图 selfie 也照收）
                            keep = bool(_worth) and _kind not in ("qr", "ad")
                        elif g:
                            # 识图没给这个字段（解析失败/旧缓存）→ 只挡二维码/广告，其余先收
                            keep = _kind not in ("qr", "ad")
                        else:
                            keep = True              # 识图整个失败：宁可先收下，别漏掉好图
                        if not keep:
                            self._log("表情包：识图觉得没梗/不适合，没收（kind=%s worth=%s desc=%s）"
                                      % (_kind or "?", _worth, g.get("desc") or "?"))
                            continue
                        it = await asyncio.to_thread(
                            stickers.add_file, path, gid, g.get("desc", ""), g.get("tags") or [],
                            "", int(cfg.get("max_store", 300) or 300), g)
                        if it:
                            self._log("表情包：收藏 %s《%s》标签=%s"
                                      % (it["id"], it.get("desc"), ",".join(it.get("tags") or [])))
                            # 顺手加进那个 QQ 号自己的表情收藏（手机上就能看到）
                            try:
                                if await asyncio.to_thread(stickers.add_face, path):
                                    it["face_pushed"] = True
                                    self._log("表情包：已加进 QQ 表情收藏 %s" % it["id"])
                            except Exception as _e3:
                                self._log("⚠️ 表情包：加进 QQ 表情面板失败（本地已存，面板没同步）%r" % (_e3,))
                            await self._maybe_say_about_sticker(event, it)
                    finally:
                        try:
                            os.remove(path)
                        except Exception:
                            pass
            try:
                asyncio.get_event_loop().create_task(_run())
            except Exception:
                pass
        except Exception as e:
            self._log_debug("表情包收集调度失败: %r" % (e,))

    async def _maybe_say_about_sticker(self, event, it: dict) -> None:
        """收到一张有意思的图时，偶尔（不是每次都）说一句——大部分时候什么都不说。"""
        try:
            cfg = self.cfg.get("stickers") or {}
            gid = str(event.get_group_id() or "")
            if gid in [str(x) for x in (cfg.get("send_exclude_groups") or [])]:
                return
            import random as _r
            if _r.random() > float(cfg.get("collect_say_prob", 0.18) or 0):
                return
            now = time.time()
            lst = getattr(self, "sticker_say_ts", None)
            if lst is None:
                lst = self.sticker_say_ts = {}
            hits = lst.setdefault(gid, [])
            hits[:] = [x for x in hits if now - x <= 3600]
            if len(hits) >= int(cfg.get("collect_say_max_per_hour", 2) or 2):
                return
            if hits and now - hits[-1] < 600:      # 两次之间至少隔 10 分钟，别连着说
                return
            lines = cfg.get("collect_say_lines") or ["好图", "这张有意思，偷了", "偷了", "笑死，存了"]
            prev = getattr(self, "sticker_say_line", None)
            if prev is None:
                prev = self.sticker_say_line = {}
            cand = [x for x in lines if x != prev.get(gid)] or lines
            line = _r.choice(cand)
            hits.append(now)
            prev[gid] = line
            await event.send(MessageChain([Plain(line)]))
            self._remember_bot_line(event, line)
            self._log("表情包：收藏后开口说了 %r" % line)
        except Exception as e:
            self._log_debug("表情包：想夸一句没发出去 %r" % (e,))

    async def _maybe_sticker(self, event) -> None:
        """回复里带 [表情] / [表情:无语] 标记 → 换一张合适的收藏图发出去。"""
        import re as _re
        try:
            cfg = self.cfg.get("stickers") or {}
            if not cfg.get("enabled", True):
                return
            gid = self._chat_key(event)
            result = event.get_result()
            chain = list(getattr(result, "chain", None) or [])
            plains = [c for c in chain if isinstance(c, Plain)]
            if not plains:
                return
            alltext = "".join(str(c.text or "") for c in plains)
            m = _re.search(r"\[表情(?::([^\]\n]{1,24}))?\]", alltext)
            if not m:
                return

            def _strip():
                for c in plains:
                    c.text = _re.sub(r"\[表情(?::[^\]\n]{1,24})?\]", "",
                                     str(c.text or "")).strip()

            # 这个群/私聊不允许发表情 → 把标记清掉，别把 [表情:x] 当文字发出去
            _bad = (gid.startswith("p:") and not cfg.get("send_private", True)) or (
                (not gid.startswith("p:"))
                and gid in [str(x) for x in (cfg.get("send_exclude_groups") or [])])
            if _bad:
                _strip()
                return
            now = time.time()
            lst = getattr(self, "sticker_ts", None)
            if lst is None:
                lst = self.sticker_ts = {}
            hits = lst.setdefault(gid, [])
            hits[:] = [x for x in hits if now - x <= 3600]
            if len(hits) >= int(cfg.get("max_per_hour", 3) or 3):
                _strip()
                return
            hint = (m.group(1) or "").strip()
            recent = " ".join([x[1] for x in list(self.recent.get(gid) or [])[-4:]])
            cands = []
            if hint:                                  # [表情:id] → 指名要这一张
                _by_id = stickers.find(hint)
                if _by_id:
                    cands = [_by_id]
                    self._log_debug("表情包：指名 %s" % hint)
            if not cands:
                cands = stickers.pick((alltext + " " + hint + " " + recent), [hint] if hint else [])
            if not cands:
                self._log_debug("表情包：没有合适的（hint=%r）" % hint)
                _strip()
                return
            it = cands[0]
            # 账号表情要落成本地文件再发（直接发 qq_expression 直链，QQ 那边经常收不到）
            path = await asyncio.to_thread(stickers.ensure_local, it)
            if not path or not os.path.exists(path):
                self._log_debug("表情包：图没落盘，先不发（%s）" % it.get("id"))
                _strip()
                return
            _strip()
            nonempty = [c for c in chain if not (isinstance(c, Plain) and not str(c.text or "").strip())]
            try:
                if str(path).startswith("http") and hasattr(Image, "fromURL"):
                    img = Image.fromURL(path)
                elif hasattr(Image, "fromFileSystem"):
                    img = Image.fromFileSystem(path)
                else:
                    img = Image(file=path)
            except Exception:
                img = Image(file=path)
            # 图片表情单独拆一条消息发（真人不会把图塞进文字气泡里）
            try:
                asyncio.create_task(event.send(MessageChain([img])))
                result.chain = nonempty if nonempty else []
            except Exception:
                nonempty.append(img)
                result.chain = nonempty
            hits.append(now)
            stickers.mark_used(it.get("id"))
            self._log("表情包：发了 %s《%s》标签=%s（本小时第 %d 张）"
                      % (it.get("id"), it.get("desc"), ",".join(it.get("tags") or []), len(hits)))
        except Exception as e:
            self._log("表情包发送失败（忽略）: %r" % (e,))

    @staticmethod
    def _chat_key(event) -> str:
        """群聊用群号；私聊用 p:<对方QQ>。"""
        gid = str(event.get_group_id() or "").strip()
        if gid and gid != "0":
            return gid
        try:
            uid = str(event.get_sender_id() or "").strip()
        except Exception:
            uid = ""
        return ("p:" + uid) if uid else "p:unknown"

    @staticmethod
    def _poked_me(event) -> bool:
        """这条事件是不是"有人拍/戳她"。

        OneBot 的 notice→poke 会被 AstrBot 转成带 Poke 组件的群消息，id=被戳的人。
        个别协议实现会把 id 填成 0/空/戳的人自己，这种也当作"戳她"（有频率限制兜着）。
        """
        if Poke is None:
            return False
        try:
            me = str(event.get_self_id() or "")
            sender = str(event.get_sender_id() or "")
            for m in event.get_messages():
                if not isinstance(m, Poke):
                    continue
                tid = str(getattr(m, "id", "") or "").strip()
                tqq = str(getattr(m, "qq", "") or "").strip()
                if tid == me or tqq == me:
                    return True
                if tid in ("", "0", "None") or tid == sender:
                    return True       # 拿不到目标就当作戳她
        except Exception:
            pass
        return False

    @staticmethod
    def _pts_all_ok(sess: dict) -> bool:
        return bool(sess) and bool(sess.get("active"))

    @staticmethod
    def _looks_solved(text: str, bottom: str) -> bool:
        """玩家这段话是不是已经把汤底说出来了（用二字覆盖率粗判，零成本）。"""
        try:
            t = re.sub(r"[\W_]+", "", str(text or ""))
            b = re.sub(r"[\W_]+", "", str(bottom or ""))
            if len(t) < 30 or len(b) < 20:
                return False
            grams = {b[i:i + 2] for i in range(len(b) - 1)}
            if not grams:
                return False
            hit = sum(1 for g in grams if g in t)
            return hit / float(len(grams)) >= 0.55
        except Exception:
            return False

    async def _allow(self, event: AstrMessageEvent) -> None:
        # 关键：让后面的 LLM 流程认为"这条消息该回"
        event.is_wake = True
        event.is_at_or_wake_command = True
        # agent loop 模式：这一轮由我们自己驱动（失败就照旧交给 AstrBot，不会沉默）
        if self._agent_loop_on(event):
            try:
                if await self._agent_loop_run(event):
                    event.stop_event()
            except Exception as e:
                self._log("agent loop 出错，回退原流程: %r" % (e,))
                self._fault("loop_fallback", repr(e))

    def _at_me(self, event: AstrMessageEvent) -> bool:
        me = str(event.get_self_id())
        try:
            for msg in event.get_messages():
                if isinstance(msg, At) and str(msg.qq) == me:
                    return True
        except Exception:
            pass
        return False

    @filter.event_message_type(_PRIVATE_ET)
    async def gate_private(self, event: AstrMessageEvent) -> None:
        """私聊：只在开了 agent loop 的会话里接管（其他私聊完全不碰）。"""
        try:
            self._reload_cfg()
            if self._agent_loop_on(event):
                await self._allow(event)
        except Exception as e:
            self._log("私聊闸门出错（忽略）: %r" % (e,))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def gate(self, event: AstrMessageEvent) -> None:
        try:
            self._reload_cfg()
            cfg = self.cfg
            try:
                _a = self.cfg.get("agent") or {}
                if _a.get("loop_mode") or _a.get("send_tools"):
                    self._log("agent配置: loop_mode=%s loop_groups=%s send_tools=%s send_groups=%s"
                              % (_a.get("loop_mode"), _a.get("loop_groups"),
                                 _a.get("send_tools"), _a.get("send_tools_groups")))
            except Exception:
                pass
            try:
                self._t_in[event.unified_msg_origin] = time.time()
                if len(self._t_in) > 200:
                    self._t_in.clear()
            except Exception:
                pass
            text = event.message_str or ""
            self._remember(str(event.get_group_id() or ""), event, text)
            self._touch(str(event.get_group_id() or ""),
                        getattr(getattr(event, "message_obj", None), "message_id", None))
            self._log_chat(event, text)
            # 注意：必须先取"上一条"，再记录本条，否则复读检测会拿自己跟自己比
            gid0 = str(event.get_group_id() or "")
            uid0 = str(event.get_sender_id() or "")
            prev_text = ""
            if not self._in("no_context_groups", gid0):
                prev_text = self.sender_text.get((gid0, uid0), "")
                self._note_sender(gid0, uid0, text)

            # 把"同一人刚连发的几句"拼成完整一句：判断"是不是在叫她"要用完整的那句
            # （真人常把 @她 + 正文 拆成两条发，只看最后一条会漏掉 @）
            burst_text = text
            try:
                if gid0:
                    items0 = list(self.recent.get(gid0) or [])
                    who_now0 = ""
                    try:
                        who_now0 = str(event.get_sender_name() or event.get_sender_id() or "")
                    except Exception:
                        pass
                    tail0 = []
                    while items0 and len(items0[-1]) > 2 and items0[-1][2] == str(event.get_sender_id() or ""):
                        tail0.insert(0, items0.pop()[1])
                    if not tail0 and who_now0:            # 兜底：老数据没有 QQ 号时按昵称
                        while items0 and items0[-1][0] == who_now0:
                            tail0.insert(0, items0.pop()[1])
                    if tail0:
                        burst_text = " ".join([x for x in tail0 if x]).strip() or text
            except Exception:
                pass
            gid = str(event.get_group_id() or "")
            # ---- 有人"拍一拍/戳一戳"她 → 用一句短反应接住（不走 LLM）----
            try:
                _pc = self.cfg.get("poke_reply") or {}
                # 调试：只要是 Poke 组件就记一笔（看看到底是谁戳谁）
                try:
                    for _m in event.get_messages():
                        if Poke is not None and isinstance(_m, Poke):
                            self._log("收到 Poke 事件：poke.id=%r poke.qq=%r self=%r gid=%r sender=%r"
                                      % (getattr(_m, "id", None), getattr(_m, "qq", None),
                                         event.get_self_id(), gid, event.get_sender_id()))
                except Exception:
                    pass
                if _pc.get("enabled", True) and gid and self._poked_me(event):
                    _now = time.time()
                    _pk = getattr(self, "poke_ts", None)
                    if _pk is None:
                        _pk = self.poke_ts = {}
                    _lst = _pk.setdefault(gid, [])
                    _lst[:] = [x for x in _lst if _now - x <= 3600]
                    _last = getattr(self, "poke_last", None)
                    if _last is None:
                        _last = self.poke_last = {}
                    _ok = (len(_lst) < int(_pc.get("max_per_hour", 4) or 4)
                           and (_now - float(_last.get(gid, 0) or 0)) >= float(_pc.get("min_interval_sec", 20) or 20))
                    if _ok:
                        _lst.append(_now)
                        _last[gid] = _now
                        _lines = _pc.get("lines") or ["？", "干嘛", "别戳"]
                        _prevline = getattr(self, "poke_line", None)
                        if _prevline is None:
                            _prevline = self.poke_line = {}
                        _cand = [x for x in _lines if x != _prevline.get(gid)]
                        _line = random.choice(_cand or _lines)
                        _prevline[gid] = _line
                        await event.send(MessageChain([Plain(_line)]))
                        self._remember_bot_line(event, _line)
                        self._log("拍一拍：回了 %r（本小时第 %d 次）" % (_line, len(_lst)))
                    else:
                        self._log_debug("拍一拍：超过频率限制，不回应")
                    event.stop_event()
                    return
            except Exception as _e:
                self._log("拍一拍处理失败（忽略）: %r" % (_e,))
            # ---- 海龟汤模式（全程 GLM，DS 链路不触发）----
            try:
                if await self._turtle_handle(event, burst_text or text, gid, cfg):
                    return
            except Exception as _e:
                self._log("海龟汤入口异常: %r" % (_e,))

            # ---- 新群自动建档（必须在白名单检查之前：新群要先建卡并被登记，才谈得上放行）----
            try:
                await self._ensure_group_card(gid, event)
            except Exception as _e:
                import traceback as _tb
                self._log("自动建档调用异常: %r | %s"
                          % (_e, _tb.format_exc()[-200:].replace("\n", " ")))

            # ---- 群白名单：不在名单里的群，她完全忽略（不记录、不回复、不进记忆）----
            # 只对真实 QQ 号生效，测试连接（self_id 不同）不受限
            try:
                _allowed = [str(x) for x in (cfg.get("allowed_groups") or [])]
                _real = str(cfg.get("allowed_self_id") or "") == str(event.get_self_id() or "")
            except Exception:
                _allowed, _real = [], False
            if _allowed and _real and str(gid) not in _allowed:
                self._log_debug("群 %s 不在白名单 → 完全忽略" % gid)
                event.stop_event()
                return

            # 有人发图 → 后台识图收进表情包收藏夹（这里每条消息都会经过）
            self._spawn_sticker_collect(event)

            # ---- 连发合并：真人常把一句话拆成几条发 ----
            # 短消息、或他刚发过言 → 先等 burst_debounce_sec，若他又发了新消息，就让后面那条来处理
            # （这样她看到的是完整的一整句，也只回一条；等待不影响 token）
            try:
                deb = self._reply_delay(text, gid, str(event.get_sender_id() or "")) if gid else 0.0
            except Exception:
                deb = 0.0
            self._log_debug("delay 计算: deb=%.2fs 文本长度=%d" % (deb, len(text or "")))
            if deb > 0:
                now0 = time.time()
                udev = str(event.get_sender_id() or "")
                await asyncio.sleep(deb)
                # 等待期间他又接着说了 → 这条不处理，交给后面那条（把几句合起来当一整句）
                st_later = list(self.sender_times.get(gid) or [])
                newer = [ts for ts, u in st_later if ts > now0 + 0.05 and str(u) == udev]
                if not newer:      # 兜底（该群不记 sender_times 时，看任意新消息）
                    newer = [ts for ts in (self.msg_times.get(gid) or []) if ts > now0 + 0.05]
                if newer:
                    self._log("拟人延迟 %.1fs 期间他还在发 → 这条跳过，交给后面那条合并" % deb)
                    event.stop_event()
                    return

            # 识别"对方一个字一个字发过来"（近 20 秒内他连着发了 ≥4 条超短消息）
            try:
                t_now = time.time()
                my_lens = [x for x in (self.msg_lens.get(gid) or [])
                           if t_now - x[0] <= 20 and x[1] == str(event.get_sender_id() or "")]
                short_n = len([1 for x in my_lens if x[2] <= 3])
                if short_n >= 4:
                    if not hasattr(self, "_mirror_char"):
                        self._mirror_char = {}
                    self._mirror_char[event.unified_msg_origin] = short_n
            except Exception:
                pass

            # 群友点名要求"一个字一个字回" → 标记这条回复走逐字模式
            try:
                cm = ((self.cfg.get("split_reply") or {}).get("char_mode") or {})
                if cm.get("enabled", True) and burst_text:
                    for kw in (cm.get("triggers") or []):
                        if kw and kw in burst_text:
                            if not hasattr(self, "_char_mode"):
                                self._char_mode = {}
                            self._char_mode[event.unified_msg_origin] = True
                            self._log("逐字模式：本条回复将一个字一条发（触发词 %r）" % kw)
                            break
            except Exception:
                pass

            # 「只认 @」的群：工具模式。除了被 @，什么都不回（不叫名字、不接话、不看概率）
            if gid and gid in [str(x) for x in (cfg.get("only_at_groups") or [])]:
                if self._at_me(event):
                    self._log("工具模式：被 @ → 回复")
                    await self._allow(event)
                else:
                    self._log_debug("工具模式：非 @ → 不回")
                    event.stop_event()
                return
            nick = await self._bot_nickname(event)
            self._log_debug("nick=%r text=%r at=%s offpeak=%s" % (nick, text[:20], self._at_me(event), in_offpeak(cfg)))
            kws = list(cfg.get("keywords") or [])
            if nick:
                kws.append(nick)
                # 昵称里的「-哈」「(xxx)」之类后缀也拆出来匹配
                for part in nick.replace("（", "(").split("(")[0].split("-"):
                    if len(part.strip()) >= 2:
                        kws.append(part.strip())
            called = any(k and k in burst_text for k in kws)
            if self._at_me(event) or called:
                await self._allow(event)           # 被 @ 或 被叫到名字 → 回复（高峰也放行）
                return
            offpeak = in_offpeak(cfg)
            if offpeak and event.is_at_or_wake_command:
                await self._allow(event)           # 低峰：被引用/唤醒词也放行
                return
            if not offpeak:
                if text.lstrip().startswith("/"):
                    await self._allow(event)       # 斜杠指令放行
                    return
                # 高峰豁免：她刚刚说过话，且这条是在回应她
                # （同一个人接着说 / 引用她的消息 / 内容在接她的话）→ 放行，交给打分流程
                uid_p = str(event.get_sender_id() or "")
                now_p = time.time()
                last_p = self.last_reply_ts.get(gid, 0.0)
                win_p = float(cfg.get("cont_window_sec", 180))
                related = False
                if last_p and 0 <= now_p - last_p <= win_p:
                    to_p = str(self.last_reply_to.get(gid) or "")
                    related = bool(to_p and to_p == uid_p) or self._quotes_me(event)
                    if not related:
                        mine = [x[1] for x in (self.recent.get(gid) or []) if str(x[0]) == "我"]
                        if mine:
                            try:
                                need = float((cfg.get("scoring") or {}).get("echo_strong", 0.34))
                            except Exception:
                                need = 0.34
                            related = scoring._overlap(text, mine[-1]) >= need
                if not related:
                    self._log_debug("高峰：非 @ 且不在对话中 → 不回")
                    event.stop_event()
                    return
                self._log("高峰豁免：她刚说过话且这条在回应她 → 交给打分（仍受冷却/额度约束）")
            gid = str(event.get_group_id())
            uid = str(event.get_sender_id() or "")
            now = time.time()
            hour = int(now // 3600)
            cap = int(cfg.get("max_auto_per_hour", 6))
            used = self.hour_count.get((gid, hour), 0)
            if used >= cap:                              # 硬闸门 1：本小时额度用完
                self._log("跳过(本小时额度用完 %s/%s) 文本=%r" % (used, cap, text[:24]))
                event.stop_event()
                return

            # 硬闸门 2：冷却。她刚说完话、紧接着有人接话时用短冷却（默认 30s），否则 3 分钟
            last_reply = self.last_reply_ts.get(gid, 0.0)
            engaged = bool(last_reply) and (now - last_reply) <= float(cfg.get("engaged_window_sec", 90))
            flood_now = False
            if self.scorer is not None and self.scorer.enabled():
                try:
                    flood_now = self.scorer.flooding(list(self.sender_times.get(gid) or []), uid, now)[0]
                except Exception:
                    flood_now = False
            streak, streak_ts = self.engaged_streak.get(gid, (0, 0.0))
            if now - streak_ts > 300:
                streak = 0                            # 5 分钟没接话 → 连击清零
            # 连续接话最多 max_engaged_streak 次，超了就退回普通冷却，免得她连珠炮
            # 对方在刷屏时不算"在跟我聊天"：不给快速通道
            # 冷却只跟"在不在对话里"有关；刷屏只影响"值不值得接"（概率），不再偷偷改冷却
            engaged_ok = engaged and streak < int(cfg.get("max_engaged_streak", 6))
            if engaged_ok:
                cooldown = float(cfg.get("engaged_interval_sec", 6))      # 对话中：可以接得快
            elif engaged:
                cooldown = float(cfg.get("engaged_rest_sec", 60))         # 连击用完：还在聊，但收着
            else:
                cooldown = float(cfg.get("min_interval_sec", 180))        # 没在聊：3 分钟一次
            last_out = max(self.last_auto.get(gid, 0.0), last_reply)
            if now - last_out < cooldown:
                self._log("跳过(冷却中 还需%.0fs，engaged=%s 刷屏=%s 连击=%s) 文本=%r"
                          % (cooldown - (now - last_out), int(engaged), int(flood_now),
                             streak, text[:24]))
                event.stop_event()
                return

            # 同一人连发：她已经回过这个人，而这条又紧接着他自己发的 → 不逐条回（只回一条）
            try:
                bsec = float(cfg.get("burst_suppress_sec", 60))
            except Exception:
                bsec = 60.0
            if bsec > 0:
                prev_sp = str(self.prev_speaker.get(gid, "") or "")
                replied_to = str(self.last_reply_to.get(gid) or "")
                since_rep = now - float(self.last_reply_ts.get(gid, 0.0))
                if prev_sp == uid and replied_to == uid and 0 <= since_rep <= bsec:
                    self._log("跳过(同一人连发，这轮只回一条) 文本=%r" % text[:24])
                    event.stop_event()
                    return

            # 零 token 打分：算"这条值不值得回"，只调概率，不看内容语义
            p = None
            why = []
            if self.scorer is not None and self.scorer.enabled():
                self.cfg["_hour_ratio"] = (used / float(cap)) if cap else 0.0
                p, why = self.scorer.probability(
                    text=text,
                    recent=list(self.recent.get(gid) or []),
                    msg_times=[x for x in (self.msg_times.get(gid) or [])],
                    last_reply_ts=last_reply,
                    last_reply_to=self.last_reply_to.get(gid, ""),
                    sender_id=uid,
                    sender_times=list(self.sender_times.get(gid) or []),
                    mentions_other=self._at_other(event),
                    sender_last_text=prev_text,
                    quotes_me=self._quotes_me(event),
                    offpeak=offpeak,
                    now=now,
                )
            if p is None:                                # 打分器关掉/出错 → 退回固定概率
                p = float(cfg.get("auto_reply_probability", 0.25))
            if bool((cfg.get("scoring") or {}).get("log_decisions", True)):
                self._log("决策 p=%.2f 额度%s/%s engaged=%s 连击=%s 刷屏=%s 文本=%r ← %s"
                          % (p, used, cap, int(engaged), streak, int(flood_now), text[:24],
                             "、".join(why) or "无信号(基础概率)"))

            if random.random() >= p:                     # 硬闸门 3：抽签
                event.stop_event()
                return
            self.hour_count[(gid, hour)] = used + 1
            self.last_auto[gid] = now
            self.engaged_streak[gid] = (streak + 1 if engaged_ok else 0, now)
            await self._allow(event)               # 放行 → 走正常 LLM 回复
            return
        except Exception:
            import traceback
            try:
                self._log("gate 异常（本条不回）: %s"
                          % traceback.format_exc()[-400:].replace("\n", " "))
            except Exception:
                pass
            event.stop_event()               # 插件出错就不要回，避免刷屏
