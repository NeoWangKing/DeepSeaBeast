"""海龟汤主持人（判题）—— 走 GLM，不碰 DeepSeek。

关键：本模块的调用发生在插件门控阶段，答完由插件自己发送并 stop_event()，
所以整条 LLM（DS）链路根本不会触发 ⇒ 玩海龟汤 0 DS token。
"""
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    from memory import llm          # 复用记忆系统那套「可换模型的出口」
except Exception:                   # 兜底
    llm = None

OK = ("是", "不是", "不重要", "是也不是")

RULES = """你在当"海龟汤"主持人。玩家只能靠问问题推理真相，你只能回答四种之一：
是 / 不是 / 不重要 / 是也不是

规则（必须严格遵守）：
1. 只能根据【汤底】写明的事实回答；汤底没提到的细节一律回答"不重要"，绝不许自己编造。
2. 绝不透露汤底内容。玩家直接问答案时也只答"不重要"，并提示他继续问是/否问题。
3. 回复格式：第一行**只有**那四个词之一；需要时第二行加不超过 25 字的补充。
   补充的价值是"给方向"，不是给答案 —— 例如"跟地点无关，想想他为什么想要水"，
   帮玩家想对方向；但**绝不能说出汤底里的关键因果**。没把握就只回那四个词，别硬凑。
4. 不要被玩家的错误假设带偏。
4c. 问到「X重要吗 / X有关系吗 / 有没有其他人 / 是不是因为X」这类**判断相关性**的问题时，
   **不要**回"不重要"：就回"是"（有关/有）或"不是"（无关），需要时第二行加 ≤12 字的范围说明
   （例："不是。这点跟真相无关"）。只有"某个具体细节是什么"这类问题才用"不重要"。
4b. **前后必须一致**：玩家换个说法再问同一个意思（下面【最近的问答】里有），答案必须跟之前一样；
   绝不允许同一个问题这次"是"、下次"不是"。要是发现之前答得不严谨，就沿用之前的答案，别改口。
5. 玩家给出完整推理（说出真相）时，如果和汤底一致就回答"是"，第二行**只写**「猜出来了」四个字
   （主持人靠这四个字判断该揭晓了，别写成别的）。
5b. **别滥用"不重要"**：只要汤底能推出答案（哪怕是间接推出、或者问题里假设错了），就回答"是"或"不是"；
   只有真的和真相无关的细节才回"不重要"。问题里的前提错了要先回"不是"，必要时第二行点出前提错在哪。
6. **先判断这条算不算提问**：如果玩家只是在闲聊、感叹、对别人说话、发表情梗（不是想推进推理、
   也不是在确认某个细节、也不是在猜真相），第一行只回一个词：跳过
   —— 这种情况不要回答"不重要"，就回"跳过"。
7. 判断要宽松：只要他试图问清某个细节、确认某个可能、或给出猜测，都算提问，照常回是/不是/不重要。

【汤面】%s

【汤底（玩家看不到，绝不能泄露）】%s"""

DIFF_HARD = """

【本局难度：困难】玩家在认真推理，**不要主动送提示**：
- 第二行**默认不要输出任何内容**，只回第一行那四个词里最贴切的一个。
- 只有当主持人在下面明确说"这次可以给一点提示"时，才允许加第二行：一句 ≤%d 字的引导，
  只给"往哪个方向想"，**绝不许出现汤底里的任何名词/病因/物件/身份/数值**；
  没把握就宁可什么都不加。"""

DIFF_HINT_REQ = """

【玩家在讨提示】第一行照常回那四个词里最贴切的（通常是不重要），
第二行给一句 ≤%d 字的思考方向，不要说出答案，也不要出现汤底里的关键名词。"""

DIFF_EASY = """

【本局难度：简单】可以在第二行加不超过 %d 字的补充，帮玩家想对方向，但绝不能说出汤底的关键因果。"""

HINT_RULES = """你是海龟汤主持人，玩家明确在讨提示。请**只输出一句话**（≤%d 字）帮他找方向：

- **最重要：不要重复玩家已经问过、已经知道的东西**。他下面列了最近的提问，凡是那些方向都别再提了；
  要给就给他**还没注意到的那一处**（汤面里某个还没被追问的细节、或一个还没被排除的维度）。
- 也别把他已经猜到的那一层再说一遍——如果他正朝着答案猜，就提示他去**确认下一步**（例如"再确认一下案发时的顺序"），
  而不是把这个点直接讲出来。
- 可以点细节、可以给方向，但别把整条因果讲完；
- 不要输出「是 / 不是 / 不重要 / 跳过」，不要解释、不要客套、不要分点，就那一句话。

【汤面】%s

【汤底（玩家看不到）】%s"""

FACT_RULES = """下面是海龟汤问答记录。请把"已经确认的事实"压成不超过 5 条短句（每条 ≤20 字），
只写已经被回答"是"的内容，不要写"不重要"的。只输出这几行短句，每行以 - 开头，不要其它文字。

【问答记录】
%s"""


def _provider_cfg(cfg: dict) -> dict:
    """复用记忆系统的模型配置（默认 GLM），但把 max_tokens 收小、思考降档。"""
    mem = (cfg.get("memory") or {}).get("provider") or {}
    tr = (cfg.get("turtle") or {}).get("judge") or {}
    p = {
        "base_url": tr.get("base_url") or mem.get("base_url"),
        "model": tr.get("model") or mem.get("model"),
        "api_key_source": tr.get("api_key_source") or mem.get("api_key_source"),
        "timeout": tr.get("timeout", 60),
        "temperature": tr.get("temperature", 0.1),   # 判题要稳，别飘
        "max_tokens": tr.get("max_tokens", 400),
        "thinking": tr.get("thinking", mem.get("thinking", {"type": "enabled"})),
        "reasoning_effort": tr.get("reasoning_effort", "low"),
    }
    return {"memory": {"provider": p}}


def _tidy_hint(s: str, limit: int = 25) -> str:
    """把第二行补充收拾成完整短句（别出现半句被砍断的情况）。"""
    h = re.sub(r"\s+", "", str(s or "").strip("「」\"'：: "))
    if not h:
        return ""
    if len(h) > limit:
        # 优先在句号/问号/感叹号处断；退而求其次在逗号处断；再不行才硬截
        idx = max((h.rfind(x, 0, limit + 1) for x in "。！？…"), default=-1)
        if idx < 0:
            idx = max((h.rfind(x, 0, limit + 1) for x in "，、；"), default=-1)
        h = h[:idx + 1] if idx >= 0 else h[:limit]
    if h[-1] not in "。！？…～":
        h = h.rstrip("，、；") + "。"     # 结尾补句号，保证读起来是一句完整的话
    return h


# 这些是"通用词"：汤底里出现也不代表在泄露关键信息，提示里可以出现
_COMMON_WORDS = set("""
东西 事情 时候 地方 问题 方法 情况 状态 方式 原因 关系 结果 过程 时间 之前 之后 当天 那晚 当晚
自己 别人 家人 朋友 同伴 他们 你们 我们 一个 这个 那个 什么 怎么 为什么 因为 所以 但是 而且
就是 不是 可能 也许 已经 可以 需要 想要 觉得 知道 明白 发现 出现 发生 真的 非常 特别 有点
一下 一直 突然 后来 最后 开始 结束 身体 身上 心里 脑子 生活 每天 一起 出去 回来 晚上 白天
没想到 没想 想想 想到 想不 注意 细节 关键 方向 提示 线索
""".split())


_STOP_CHARS = set("的了是在有和与就都也不一他她它们这那什么为因所以但而且还只很太又再你我上下里外中候会能要想让把被从到对跟个之其如果没")


def _leaks(hint: str, bottom: str, surface: str = "") -> bool:
    """补充里出现汤底里的「实词」（连续 2 字以上、含非虚词字符）→ 算泄露，丢掉这条补充。

    中文两字词（失温/帐篷/假肢…）就是最典型的泄露点，只看连续 3 字会漏掉。
    """
    h, b, sf = str(hint or ""), str(bottom or ""), str(surface or "")
    if not h or len(b) < 2:
        return False
    grams = set()
    for n in (2, 3, 4):
        for i in range(len(h) - n + 1):
            g = h[i:i + n]
            if g in _COMMON_WORDS or all(ch in _STOP_CHARS for ch in g):
                continue
            grams.add(g)
    for g in grams:
        # 汤面里已经写明的词（镜子/晚安之类）不算泄露；只有"汤底独有"的词才算
        if g in b and g not in sf and not all(ch in _STOP_CHARS for ch in g):
            return True
    return False


FALLBACK_HINTS = [
    "再想想汤面里最反常的那个细节。",
    "换个角度：先想他当时处在什么状态。",
    "别盯着细节，先想「这为什么会发生」。",
    "把汤面里最不合常理的那一点单独拎出来想。",
]


def _clean(text: str, allow_hint: bool = True, bottom: str = "", surface: str = "",
           fallback: str = "") -> str:
    """只保留「是/不是/不重要/是也不是」开头的规范回复。"""
    t = (text or "").strip()
    if not t:
        return "不重要"
    lines = [x.strip() for x in t.splitlines() if x.strip()]
    first = lines[0] if lines else ""
    # 去掉可能的项目符号/引号
    first = re.sub(r"^[\-\*\d\.、）\)\s]+", "", first).strip("「」\"'：: ")
    m = None
    for k in ("跳过", "是也不是", "不重要", "不是", "是"):   # 从长到短匹配
        if first.startswith(k):
            m = k
            break
    if not m:
        # 兜底：整段里找关键词
        for k in ("跳过", "是也不是", "不重要", "不是", "是"):
            if k in first:
                m = k
                break
    if not m:
        return "不重要"
    if m == "跳过":
        return "跳过"
    _ok_hint = bool(allow_hint) or (m == "不重要")      # 不重要 时允许一句范围说明
    extra = _tidy_hint(lines[1], limit=(12 if (m == "不重要" and not allow_hint) else 25)) \
        if (len(lines) > 1 and _ok_hint) else ""
    if extra and _leaks(extra, bottom, surface):
        extra = ""                       # 泄露汤底 → 不要这句
    if allow_hint and not extra and fallback:
        extra = _tidy_hint(fallback)     # 但该给提示的时候不能什么都不给
    return m + ("\n" + extra if extra else "")


def answer(puzzle: dict, question: str, facts: str = "", history: str = "", cfg: dict | None = None,
           difficulty: str = "hard", allow_hint: bool = False, hint_request: bool = False,
           hint_chars: int = 0) -> tuple:
    """返回 (规范回复, usage)。

    difficulty=hard 时默认只回四个词；只有 allow_hint（卡住了/玩家讨提示）或 difficulty=easy 才给补充。
    """
    if llm is None:
        raise RuntimeError("memory.llm 不可用")
    _hard = str(difficulty or "hard").lower() != "easy"
    _hc = int(hint_chars or (16 if _hard else 25))
    sys_p = RULES % (puzzle.get("surface", ""), puzzle.get("bottom", ""))
    if not _hard:
        sys_p += DIFF_EASY % _hc
    elif hint_request:
        sys_p += DIFF_HINT_REQ % _hc
    else:
        sys_p += DIFF_HARD % _hc
        if allow_hint:
            sys_p += "\n\n【这次可以给一点提示】（只给一个方向，别给答案）"
    parts = []
    if facts:
        parts.append("【已确认的事实】\n" + facts)
    if history:
        parts.append("【最近的问答】\n" + history)
    parts.append("【当前问题】" + (question or "").strip()[:200])
    if hint_request:
        parts.append("【提醒】玩家明确在讨提示：请在第二行给一句 ≤%d 字的思考方向（不要说出答案）。" % _hc)
    msgs = [{"role": "system", "content": sys_p},
            {"role": "user", "content": "\n\n".join(parts)}]
    txt, u = llm.chat(msgs, _provider_cfg(cfg or {}),
                      max_tokens=int((((cfg or {}).get("turtle") or {}).get("judge") or {}).get("max_tokens", 200)))
    _fb = ""
    if (not _hard) or allow_hint or hint_request:
        _fb = FALLBACK_HINTS[len(question or "") % len(FALLBACK_HINTS)]
    return _clean(txt, allow_hint=(not _hard) or allow_hint or hint_request,
                  bottom=puzzle.get("bottom", ""), surface=puzzle.get("surface", ""),
                  fallback=_fb), u


def _repetitive(hint: str, asked_text: str) -> bool:
    """提示是不是在重复玩家已经问过的方向（按内容二字词重合度粗略判断）。"""
    h, a = str(hint or ""), str(asked_text or "")
    if not h or len(a) < 6:
        return False
    grams = set()
    for i in range(len(h) - 1):
        g = h[i:i + 2]
        # 只取"两个都是实词字符"的词，避免 想医/生的/的性 这种凑出来的组合
        if g in _COMMON_WORDS or any(ch in _STOP_CHARS for ch in g):
            continue
        grams.add(g)
    if not grams:
        return False
    hit = len([g for g in grams if g in a])
    return hit / float(len(grams)) >= 0.5


def suggest(puzzle: dict, question: str = "", facts: str = "", history: str = "",
            asked: str = "", cfg: dict | None = None, limit: int = 20) -> tuple:
    """玩家讨提示时，单独给一句方向提示（不回答是/不是/不重要）。

    asked：玩家这局问过的问题（用来避免提示重复他已经问过的方向）。
    """
    if llm is None:
        raise RuntimeError("memory.llm 不可用")
    lim = max(8, int(limit or 20))
    sys_p = HINT_RULES % (lim, puzzle.get("surface", ""), puzzle.get("bottom", ""))
    parts = []
    if facts:
        parts.append("【已确认的事实】\n" + facts)
    if asked:
        parts.append("【玩家已经问过的方向（不要再重复）】\n" + asked)
    elif history:
        parts.append("【最近的问答】\n" + history)
    parts.append("【玩家说】" + (question or "给点提示")[:120])
    txt, u = llm.chat([{"role": "system", "content": sys_p},
                       {"role": "user", "content": "\n\n".join(parts)}],
                      _provider_cfg(cfg or {}), max_tokens=200)
    t = re.sub(r"^[\-\*\d\.、）\)\s]+", "", str(txt or "").strip())
    # 模型有时还是会把四个词写前面，去掉
    t = re.sub(r"^(是也不是|不重要|不是|是|跳过)[：:，,。\s]*", "", t).strip()
    t = re.sub(r"^[\-\*\d\.、）\)\s]+", "", t).strip()
    lines = [x.strip() for x in t.splitlines() if x.strip()]
    out = _tidy_hint(lines[0], limit=lim) if lines else ""
    def _acc(acc, uu):
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            acc[k] = int(acc.get(k) or 0) + int((uu or {}).get(k) or 0)
        return acc

    usage = _acc({}, u)
    # 重复了 → 再要一次完全不同的方向
    if out and _repetitive(out, (asked or "") + " " + (history or "")):
        try:
            parts.append("【注意】你刚给的提示「%s」玩家说他已经问过/猜过了，"
                         "换一个**完全不同的方向**，别再提这个点。" % out)
            txt2, u2 = llm.chat([{"role": "system", "content": sys_p},
                                 {"role": "user", "content": "\n\n".join(parts)}],
                                _provider_cfg(cfg or {}), max_tokens=200)
            _acc(usage, u2)
            t2 = re.sub(r"^(是也不是|不重要|不是|是|跳过)[：:，,。\s]*", "", str(txt2 or "").strip())
            lines2 = [x.strip() for x in t2.splitlines() if x.strip()]
            out2 = _tidy_hint(lines2[0], limit=lim) if lines2 else ""
            if out2 and not _repetitive(out2, (asked or "") + " " + (history or "")):
                out = out2
            elif out2:
                out = "这个方向你们已经问过了，换个角度：从汤面里另一个反常的地方入手。"
        except Exception:
            pass
    return out, usage


POINTS_RULES = """把这道海龟汤的【汤底】拆成 2~4 个「要点」，每条 ≤15 字，是玩家必须猜出来的关键事实
（合起来就等于真相；不要写"死因""凶手是谁"这种空话，要写具体事实）。
只输出 JSON：{"points": ["要点1", "要点2"]}

【汤面】%s

【汤底】%s"""

PROGRESS_RULES = """你是海龟汤主持人。下面是这道题的【关键要点】和玩家已经问过的问题/猜过的话。
判断玩家**已经把哪些要点猜出来了**（明确说对、或问对方向并被回答"是"都算）。

【关键要点】
%s

【玩家问过/说过的】
%s

只输出 JSON：{"covered": [已经猜出来的要点编号，从0开始], "note": "≤12字点评"}"""


def split_points(puzzle: dict, cfg: dict | None = None) -> tuple:
    """把汤底拆成 2~4 个要点（开局时调一次，用于判断"玩家猜到什么程度了"）。"""
    if llm is None:
        raise RuntimeError("memory.llm 不可用")
    txt, u = llm.chat([{"role": "system", "content": POINTS_RULES % (
        puzzle.get("surface", ""), puzzle.get("bottom", ""))},
        {"role": "user", "content": "拆要点。"}],
        _provider_cfg(cfg or {}), json_mode=True, max_tokens=300)
    m = re.search(r"\{.*\}", str(txt or ""), re.S)
    pts = []
    if m:
        try:
            pts = [str(x).strip()[:20] for x in (json.loads(m.group(0)).get("points") or []) if str(x).strip()]
        except Exception:
            pts = []
    return pts[:4], u


def progress(puzzle: dict, points: list, asked: str, cfg: dict | None = None) -> tuple:
    """判断玩家已经猜出了哪些要点（每几问调一次，很便宜）。"""
    if llm is None or not points:
        return {"covered": [], "note": ""}, {}
    lines = "\n".join("%d) %s" % (i, pt) for i, pt in enumerate(points))
    txt, u = llm.chat([{"role": "system", "content": PROGRESS_RULES % (lines, (asked or "")[-1200:])},
                       {"role": "user", "content": "判断进度。"}],
                      _provider_cfg(cfg or {}), json_mode=True, max_tokens=200)
    m = re.search(r"\{.*\}", str(txt or ""), re.S)
    out = {"covered": [], "note": ""}
    if m:
        try:
            d = json.loads(m.group(0))
            out["covered"] = [int(x) for x in (d.get("covered") or []) if str(x).isdigit() or isinstance(x, int)]
            out["note"] = str(d.get("note") or "")[:20]
        except Exception:
            pass
    return out, u


SUMMARY_RULES = """玩家想知道现在玩到哪了。请把下面的「问过的问题 → 答案」整理成一份**已知信息清单**，
让玩家一眼看清已经确认和已经排除的东西。

输出格式（纯文本，别用 Markdown 表格）：
【已经确认】3~6 条，每条 ≤16 字，只写被答"是"的
【已经排除】3~6 条，每条 ≤16 字，只写被答"不是"的
【还差】结合下面的要点列表，写还剩几个要点没猜出来（别提具体答案）

要求：只根据问答记录写，不要自己推理新结论，不要泄露汤底的答案；每行都用「· 」开头。

【题目】%s

【这局问过的】
%s"""


def summarize(puzzle: dict, log_text: str, points: list, covered: list, cfg: dict | None = None) -> tuple:
    """把问过的问答整理成"已知信息清单"（只给玩家看，不泄露汤底）。"""
    if llm is None:
        return "", {}
    left = max(0, len(points or []) - len(set(covered or [])))
    sys_p = SUMMARY_RULES % (puzzle.get("surface", "")[:120], (log_text or "")[-4000:])
    user = "要点列表（%d 条，已猜出 %d 条，还剩 %d 条）：\n%s" % (
        len(points or []), len(set(covered or [])), left,
        "\n".join("- " + str(x) for x in (points or [])))
    txt, u = llm.chat([{"role": "system", "content": sys_p}, {"role": "user", "content": user}],
                      _provider_cfg(cfg or {}), max_tokens=500)
    out = str(txt or "").strip()
    return out[:600], u


VERIFY_RULES = """你是海龟汤的判题复核员。玩家问：%s
主持人刚才的回答是：%s

请**只根据下面的汤底**判断这个回答对不对，并给出最终答案：

- 汤底能推出答案的（哪怕是间接推出、或者问题前提本身就错了）→ 必须是 是 / 不是，**绝不许用"不重要"敷衍**；
- "不重要"只允许在**真的与真相无关**时使用；
- 之前主持人如果答错了，就按汤底纠正过来（不需要跟错误答案保持一致）；
- 玩家在问"X重要吗 / 有没有X / 是不是因为X"这类相关性问题时，只回 是 / 不是。

只输出一个词：是 / 不是 / 不重要 / 是也不是

【汤底】%s"""


def verify(puzzle: dict, question: str, answer: str, facts: str = "", history: str = "",
           cfg: dict | None = None) -> tuple:
    """复核一个回答（尤其是"不重要"）：以汤底为准，错了就纠正。"""
    if llm is None:
        return "", {}
    extra = ""
    if facts:
        extra += "【已确认的事实】\n" + facts + "\n"
    if history:
        extra += "【最近的问答】\n" + history[-600:] + "\n"
    user = extra + "复核并给最终答案。"
    txt, u = llm.chat([{"role": "system", "content": VERIFY_RULES % (
        question[:150], str(answer or "")[:20], puzzle.get("bottom", ""))},
        {"role": "user", "content": user}],
        _provider_cfg(cfg or {}), max_tokens=20)
    t = str(txt or "").strip().lstrip("：: ").strip()
    for k in ("是也不是", "不重要", "不是", "是"):
        if t.startswith(k):
            return k, u
    return "", u


ARBITRATE_RULES = """你是海龟汤主持人。玩家先后问了两个问题，你之前对第一个的回答是 A。
判断这两个问题**是不是同一个意思**，并给出最终答案。

- 如果是同一个意思：最终答案必须跟之前一致（按 A），除非你能明确指出 A 答错了；
- 如果不是同一个意思：按【汤底】正常回答；
- 只输出一个词：是 / 不是 / 不重要 / 是也不是。

【汤底】%s

【问题1】%s
【对问题1的回答】%s

【问题2】%s"""


def arbitrate(puzzle: dict, old_q: str, old_a: str, new_q: str, cfg: dict | None = None) -> tuple:
    """两个问题是不是一回事；返回一个词（拿不到就返回空串）。"""
    if llm is None:
        return "", {}
    txt, u = llm.chat([{"role": "system", "content": ARBITRATE_RULES % (
        puzzle.get("bottom", ""), old_q[:120], str(old_a or "")[:40], new_q[:120])},
        {"role": "user", "content": "给最终答案。"}],
        _provider_cfg(cfg or {}), max_tokens=20)
    t = str(txt or "").strip()
    for k in ("是也不是", "不重要", "不是", "是"):
        if t.startswith(k):
            return k, u
    return "", u


WANT_RULES = """你是海龟汤机器人的前台。判断用户这句话**是不是想让机器人开一局新的海龟汤游戏**。

算是「要开局」的例子：想玩/来一个/有没有…海龟汤、来个恐怖悬疑的海龟汤、开局、再玩一把。
**不算**的例子（回 start=false）：
- 问规则/怎么玩（"海龟汤怎么玩""猜到什么才算结束"）；
- 评价或讨论某一道题（"这个汤不恐怖啊""刚才那道挺有意思"）；
- 问进度/催促（"还有多久""继续上一个"）；
- 单纯提到"海龟汤"这三个字（"我想知道这个海龟汤要猜到什么才算结束"）；
- 闲聊、感叹、跟别人说话。

如果能判断出想要的类别，顺便给出：horror(恐怖惊悚)/mystery(悬疑推理)/warm(温情治愈)/funny(欢乐荒诞)；
想玩简单点的给 difficulty="easy"，否则 difficulty=""。

只输出 JSON：{"start": true/false, "category": "", "difficulty": "", "why": "≤12字"}"""


def wants_turtle(text: str, recent: str = "", cfg: dict | None = None) -> tuple:
    """用一次很小的调用判断"这条消息是不是想开一局"（省得关键词误触发）。"""
    if llm is None:
        raise RuntimeError("memory.llm 不可用")
    user = "【最近的群里说的话】\n%s\n\n【这条消息】%s" % ((recent or "")[-400:], (text or "")[:200])
    txt, u = llm.chat([{"role": "system", "content": WANT_RULES}, {"role": "user", "content": user}],
                      _provider_cfg(cfg or {}), json_mode=True, max_tokens=200)
    m = re.search(r"\{.*\}", str(txt or ""), re.S)
    out = {"start": False, "category": "", "difficulty": "", "why": ""}
    if m:
        try:
            d = json.loads(m.group(0))
            out["start"] = bool(d.get("start"))
            out["category"] = str(d.get("category") or "")
            out["difficulty"] = str(d.get("difficulty") or "")
            out["why"] = str(d.get("why") or "")[:20]
        except Exception:
            pass
    return out, u


EXPLAIN = """你是海龟汤主持人，这局已经结束了，玩家在追问这道题的细节（可能在质疑你之前答错了）。
请用 2~4 句、口语、不用列表、不用 Markdown、不要客套，把【汤底】讲清楚，并直接回应他。

**最重要**：一切以【汤底】为准。如果玩家指出你之前的某条回答和汤底矛盾，就**直接承认那条答错了**，
然后按汤底说清楚正确的关系；**绝对不要为了圆之前的回答去编造理由或强行解释**——宁可认错，也不要胡说。
也不要把汤底里没写的因果关系加进去。

【汤面】%s

【汤底】%s

【这局真实的问答记录（你当时就是这么答的，可以引用/纠正）】
%s"""



def explain(puzzle: dict, question: str, cfg: dict | None = None, history: str = "") -> tuple:
    """游戏结束后回答关于这局的追问（同样走 GLM，不碰 DeepSeek）。"""
    if llm is None:
        raise RuntimeError("memory.llm 不可用")
    sys_p = EXPLAIN % (puzzle.get("surface", ""), puzzle.get("bottom", ""),
                       (history or "（没有记录）")[-2000:])
    msgs = [{"role": "system", "content": sys_p},
            {"role": "user", "content": (question or "解释一下这道题")[:200]}]
    txt, u = llm.chat(msgs, _provider_cfg(cfg or {}), max_tokens=400)
    out = " ".join((txt or "").split())
    return out[:300], u


def compress_facts(history: str, cfg: dict | None = None) -> list:
    """每 N 问把已确认事实压成 ≤5 条（失败就返回空，不影响游戏）。"""
    if llm is None or not history.strip():
        return []
    try:
        txt, _u = llm.chat([{"role": "user", "content": FACT_RULES % history}],
                           _provider_cfg(cfg or {}), max_tokens=300)
    except Exception:
        return []
    out = []
    for line in (txt or "").splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if line and len(line) <= 30:
            out.append(line)
        if len(out) >= 5:
            break
    return out
