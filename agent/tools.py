"""工具实现层（agent 化：模型输出=思考，动作靠调用工具）。

设计红线（照 bridge 的教训）：
  · 工具**绑定会话**：只认当前 event 的群/私聊，参数里没有"发到哪个群"。
  · 写操作（发言/发表情/收藏）统一走 main.py 给的发送回调，限频/去重/审计都在那一条通道里。
  · 纯逻辑：不 import astrbot，离线可测（tests/agent_sim.py 用桩驱动）。

每个工具返回一句**简短的中文结果**给模型看（成功说做了什么，失败说为什么没做）。
"""
import time

MAX_TEXT_CHARS = 400          # 单次发言总字数上限（防小作文刷屏）
MAX_PARTS = 4                 # 单次最多分几条


class Tools:
    def __init__(self, chat_key: str, callbacks: dict, cfg: dict = None):
        self.chat_key = chat_key
        self.cb = callbacks or {}
        self.cfg = cfg or {}
        self.sent = []             # 本轮已发出的东西（审计 + 抑制正文）
        self.finished = False
        self.finish_reason = ""
        self.started = time.time()
        self.calls = 0             # 本轮工具调用次数（main.py 用来限流）
        self.searched = []         # 本轮搜过什么（审计/防重复）
        self.skills_used = []      # 本轮展开过哪些技能（审计）
        self.errors = []

    # ---------- 发送类 ----------
    def _split(self, text: str) -> list:
        raw = str(text or "")
        parts = [x.strip() for x in raw.replace("｜", "|").split("|||")]
        parts = [x for x in parts if x]
        if not parts and raw.strip():
            parts = [raw.strip()]
        return parts[:MAX_PARTS]

    def send_message(self, text="", reply_to_id="", at_user_id="", face="") -> str:
        parts = self._split(text)
        if not parts:
            return "没发：text 是空的"
        total = sum(len(p) for p in parts)
        if total > MAX_TEXT_CHARS:
            return "没发：这条太长（%d 字），拆短一点再发" % total
        fn = self.cb.get("send_text")
        if not fn:
            return "没发：发送通道不可用"
        ok = 0
        for _i, p in enumerate(parts):
            try:
                # face 挂在最后一条上（"话说完了随手带个表情"最自然）
                _fc = str(face or "").strip() if _i == len(parts) - 1 else ""
                fn(p, reply_to_id=str(reply_to_id or ""), at_user_id=str(at_user_id or ""), face=_fc)
                self.sent.append(("text", p))
                ok += 1
            except Exception as e:
                self.errors.append("send_message: %r" % (e,))
                return "发了 %d 条后出错：%r" % (ok, e)
        return "已发出 %d 条" % ok

    def send_sticker(self, sticker_id="", mood="", reply_to_id="") -> str:
        """发一张表情。给 id 就发那张；只给 mood（情绪/场景）就让她自己从收藏里挑。"""
        sid = str(sticker_id or "").strip()
        mo = str(mood or "").strip()
        fn = self.cb.get("send_sticker")
        if not fn:
            return "没发：这个会话现在不能发表情"
        try:
            r = fn(sid, mood=mo, reply_to_id=str(reply_to_id or ""))
        except Exception as e:
            self.errors.append("send_sticker: %r" % (e,))
            return "没发：%r" % (e,)
        if not r:
            return "没发：没挑到合适的表情（可以用 list_stickers 看看库里都有啥）"
        self.sent.append(("sticker", sid or ("mood:" + (mo or "随手一张"))))
        if sid:
            return "已发出表情 %s" % sid
        return "已发出表情（按「%s」自己挑的一张）" % (mo or "随手")

    def collect_sticker(self, message_id="", note="") -> str:
        fn = self.cb.get("collect_sticker")
        if not fn:
            return "没收藏：这个会话不支持收藏"
        try:
            r = fn(str(message_id or ""), str(note or ""))
        except Exception as e:
            self.errors.append("collect_sticker: %r" % (e,))
            return "没收藏：%r" % (e,)
        return str(r or "收藏结果未知")

    # ---------- 只读类 ----------
    def get_recent_messages(self, limit=20) -> str:
        try:
            n = max(1, min(50, int(limit)))
        except Exception:
            n = 20
        fn = self.cb.get("recent_lines")
        if not fn:
            return "查不了：没有聊天记录通道"
        try:
            lines = fn(n) or []
        except Exception as e:
            return "查不了：%r" % (e,)
        if not lines:
            return "最近没有别的消息"
        return "最近 %d 条：\n%s" % (len(lines), "\n".join(str(x) for x in lines))

    def get_active_members(self) -> str:
        fn = self.cb.get("active_members")
        if not fn:
            return "查不了：没有成员通道"
        try:
            rows = fn() or []
        except Exception as e:
            return "查不了：%r" % (e,)
        if not rows:
            return "最近没有别人说话"
        return "最近说话的人：\n" + "\n".join(str(x) for x in rows)

    def lookup_memory(self, query="") -> str:
        fn = self.cb.get("memory_lookup")
        if not fn:
            return "查不了：没有记忆通道"
        try:
            txt = fn(str(query or ""))
        except Exception as e:
            return "查不了：%r" % (e,)
        return str(txt) if txt else "对这些人/话题没什么印象"

    # ---------- 上网（求证用，别拿来刷） ----------
    def web_search(self, query="", count=5) -> str:
        q = str(query or "").strip()
        if not q:
            return "没查：query 是空的"
        fn = self.cb.get("web_search")
        if not fn:
            return "没查：这个会话现在不能联网搜索"
        try:
            n = int(count or 5)
        except Exception:
            n = 5
        try:
            r = fn(q, n)
        except Exception as e:
            self.errors.append("web_search: %r" % (e,))
            return "查不了：%r" % (e,)
        try:
            self.searched.append(q)
        except Exception:
            pass
        return str(r)

    def read_url(self, url="") -> str:
        u = str(url or "").strip()
        if not u:
            return "没读：url 是空的"
        fn = self.cb.get("read_url")
        if not fn:
            return "没读：这个会话现在不能联网"
        try:
            return str(fn(u))
        except Exception as e:
            self.errors.append("read_url: %r" % (e,))
            return "读不了：%r" % (e,)

    def use_skill(self, name="") -> str:
        """把某个技能的说明书拿进上下文（按需展开，平时只占索引一行）。"""
        nm = str(name or "").strip()
        fn = self.cb.get("use_skill")
        if not fn:
            return "没有技能库"
        try:
            r = fn(nm)
        except Exception as e:
            self.errors.append("use_skill: %r" % (e,))
            return "拿不到技能：%r" % (e,)
        if not r:
            return ("没有这个技能%s。可用技能看系统提示里的【技能】那一段，"
                    "名字要一模一样" % (("：" + nm) if nm else ""))
        try:
            self.skills_used.append(nm)
        except Exception:
            pass
        return str(r)

    def list_stickers(self, query="") -> str:
        fn = self.cb.get("list_stickers")
        if not fn:
            return "查不了：表情库没开"
        try:
            rows = fn(str(query or "")) or []
        except Exception as e:
            return "查不了：%r" % (e,)
        if not rows:
            return "表情库里没有匹配的"
        return "匹配到 %d 张：\n%s" % (len(rows), "\n".join(str(x) for x in rows))

    # ---------- 看图 / 拍一拍 / 写记忆 ----------
    def view_image(self) -> str:
        """看当前这批消息里的图（她真的"看"：交给 DeepSeek 多模态）。"""
        fn = self.cb.get("view_image")
        if not fn:
            return "看不了：这个会话没开看图"
        try:
            txt = fn()
        except Exception as e:
            return "看不了：%r" % (e,)
        return str(txt) if txt else "这张图我看不出来是什么"

    def send_poke(self, target_id="") -> str:
        fn = self.cb.get("send_poke")
        if not fn:
            return "拍不了：这个会话没开拍一拍"
        try:
            ok = fn(str(target_id or ""))
        except Exception as e:
            return "拍不了：%r" % (e,)
        return "拍了一下" if ok else "拍不了（对方不在这个群？）"

    def send_face(self, name="") -> str:
        """发一个 QQ 自带小表情（用官方名字，比如 汪汪/吃糖/得意）。"""
        fn = self.cb.get("send_face")
        if not fn:
            return "发不了：这个会话没开发表情"
        nm = str(name or "").strip()
        if not nm:
            return "没发：name 是空的（写官方名字，比如 汪汪）"
        try:
            ok = fn(nm)
        except Exception as e:
            return "发不了：%r" % (e,)
        return ("发了个 %s" % nm) if ok else ("没找到这个表情：%s（用清单里的名字）" % nm)

    def view_sticker(self, sticker_id="") -> str:
        """看一张收藏表情的图（交给 DeepSeek 多模态描述）。"""
        fn = self.cb.get("view_sticker")
        if not fn:
            return "看不了：这个会话没开看图"
        sid = str(sticker_id or "").strip()
        if not sid:
            return "看不了：要先给 sticker_id（用 list_stickers 查）"
        try:
            txt = fn(sid)
        except Exception as e:
            return "看不了：%r" % (e,)
        return str(txt) if txt else "这张图我没看出是什么"

    def sticker_note(self, sticker_id="", note="") -> str:
        """给收藏表情写备注（以后你自己/她挑图时靠它认）。"""
        fn = self.cb.get("sticker_note")
        if not fn:
            return "写不了：这个会话没开表情备注"
        sid, nt = str(sticker_id or "").strip(), str(note or "").strip()
        if not sid or not nt:
            return "没写：sticker_id 和 note 都要给"
        try:
            ok = fn(sid, nt)
        except Exception as e:
            return "写不了：%r" % (e,)
        return "备注写好了：%s → %s" % (sid, nt) if ok else "没找到这张表情：%s" % sid

    def cancel_wake(self, scope="") -> str:
        """取消定时：scope=all 清掉这个会话的全部定时。"""
        fn = self.cb.get("cancel_wake")
        if not fn:
            return "取不了：这个会话没开定时"
        try:
            n = int(fn(str(scope or "")) or 0)
        except Exception as e:
            return "取不了：%r" % (e,)
        return ("取消了 %d 条定时" % n) if n else "没有待办的定时"

    def schedule_wake(self, after_sec="", say="", reason="", mode="auto", every_sec="") -> str:
        """定时叫醒自己：过一会儿把 say 这句话发出来。"""
        fn = self.cb.get("schedule_wake")
        if not fn:
            return "定不了：这个会话没开定时唤醒"
        try:
            sec = int(float(str(after_sec or "0")))
        except Exception:
            sec = 0
        if sec < 5:
            return "定不了：after_sec 至少要 5 秒（写秒数，比如 300 = 5 分钟后）"
        if sec > 7200:
            sec = 7200
        txt = str(say or "").strip()
        md = str(mode or "auto").strip().lower()
        ev = str(every_sec or "").strip()
        every = 0
        if ev:
            try:
                every = int(float(ev))
            except Exception:
                every = 0
            if every and every < 60:
                return "定不了：every_sec 最少 60 秒（循环任务别定太密）"
        if md not in ("auto", "say", "think"):
            md = "auto"
        if md == "auto":
            md = "say" if txt else "think"
        if md == "say" and not txt:
            return "定不了：say 模式要写清到时说的话（或写 [拍一拍]/[表情:id]）"
        if md == "think" and not (txt or str(reason or "").strip()):
            return "定不了：think 模式至少要给 reason，说明到时想干什么"
        try:
            return str(fn(sec, txt[:200], str(reason or "")[:60], md, every) or "定好了")
        except Exception as e:
            return "定不了：%r" % (e,)

    def memory_append(self, text="", kind="impression") -> str:
        """自己往记忆里写一条：印象 / 没聊完的话题 / 想说没说的话。"""
        t = str(text or "").strip()
        if not t:
            return "没记：text 是空的（把要记的一句话放进 text）"
        fn = self.cb.get("memory_append")
        if not fn:
            return "记不了：这个会话没开记忆写入"
        try:
            return str(fn(str(kind or "impression"), t[:120]) or "记下了")
        except Exception as e:
            return "记不了：%r" % (e,)

    # ---------- 收尾类 ----------
    def finish(self, reason="") -> str:
        """标记"这轮不发言"。注意：它**不会**抑制正文——只有真的调了 send_* 才抑制。"""
        self.finished = True
        self.finish_reason = str(reason or "")[:80]
        return "本轮结束（没有要发出的内容）"

    # ---------- 状态 ----------
    @property
    def spoke(self) -> bool:
        return bool(self.sent)

    def state(self) -> dict:
        return {"chat_key": self.chat_key, "sent": list(self.sent), "calls": self.calls,
                "finished": self.finished,
                "reason": self.finish_reason, "errors": list(self.errors),
                "elapsed": round(time.time() - self.started, 2)}


def spec_list(enabled: dict = None, send_tools: bool = True) -> list:
    """给 main.py 注册用的工具清单：[(name, args, desc, method_name)]。

    enabled 里为 False 的工具不注册；send_tools=False 时不注册发送类工具（第一步只读）。
    """
    en = dict(enabled or {})

    def on(k: str, default=True) -> bool:
        return bool(en.get(k, default))

    specs = []
    if on("send") and send_tools:
        specs.append(("send_message",
                      [{"type": "string", "name": "text",
                        "description": "【必填】要说的话（**这是唯一的发言方式**，写在正文里的话不会发出去）；"
                                       "想分成多条就用 ||| 分隔（最多 4 条、总共 400 字内）"},
                       {"type": "string", "name": "reply_to_id", "description": "可选：要引用哪条消息的 id"},
                       {"type": "string", "name": "face",
                        "description": "可选：在句尾再补一个 QQ 自带表情（官方名）。想插在句子中间/插多个，"
                                       "直接在 text 里写 [QQ表情:名字]，可以写任意多个、放任意位置"}],
                      "发言：你写的正文永远不会发出去，说话只能调这个工具", "send_message"))
    if on("sticker") and send_tools:
        specs.append(("send_sticker",
                      [{"type": "string", "name": "sticker_id",
                        "description": "要发的表情 id（【可用表情包】清单里那些）"},
                       {"type": "string", "name": "mood",
                        "description": "不想指名、只想要个情绪/场景就填这个，如「无语」「笑死」「点赞」「晚安」；"
                                       "和她自己从收藏里挑一张贴切的"}],
                      "发一张收藏表情包，单独一条消息发（sticker_id 和 mood 二选一，都没有就随手挑一张）",
                      "send_sticker"))
    if on("search"):
        specs.append(("web_search",
                      [{"type": "string", "name": "query",
                        "description": "【必填】要搜的问题，用自然语言写清楚（例：IEM 科隆 2026 冠军是谁）"},
                       {"type": "string", "name": "count",
                        "description": "可选：要几条结果，默认 5、最多 10"}],
                      "联网搜索：不确定的事实、最新消息、赛事/版本/价格/某人说过什么，先搜再答",
                      "web_search"))
        specs.append(("read_url",
                      [{"type": "string", "name": "url",
                        "description": "【必填】要读的网页地址（一般是 web_search 返回里的某个网址）"}],
                      "读一个网页的正文，核对原文（别只看搜索标题就下结论）", "read_url"))
    if on("skills"):
        specs.append(("use_skill",
                      [{"type": "string", "name": "name",
                        "description": "技能名，必须和系统提示【技能】里列的一模一样（例：turtle_soup）"}],
                      "把某个技能的完整说明书拿进来看（要用到某项「本事」的细节时先调它）", "use_skill"))
    if on("recent"):
        specs.append(("get_recent_messages",
                      [{"type": "string", "name": "limit", "description": "看几条，默认 20，最多 50"}],
                      "往前翻聊天记录，看看刚才大家在聊什么", "get_recent_messages"))
    if on("members"):
        specs.append(("get_active_members", [],
                      "看看最近都是谁在说话", "get_active_members"))
    if on("memory"):
        specs.append(("lookup_memory",
                      [{"type": "string", "name": "query",
                        "description": "可选：人名或话题关键词，留空则看当前的群印象与在场人物档案"}],
                      "翻你自己的群印象和人物档案", "lookup_memory"))
    if on("stickers_list"):
        specs.append(("list_stickers",
                      [{"type": "string", "name": "query", "description": "可选：关键词/情绪，留空则列常用的"}],
                      "搜你的收藏表情（返回 id、备注、标签）", "list_stickers"))
    if on("collect") and send_tools:
        specs.append(("collect_sticker",
                      [{"type": "string", "name": "message_id", "description": "【必填】那条消息的 id"},
                       {"type": "string", "name": "note", "description": "一句简短备注（以后靠它认图）"}],
                      "把群友刚发的有意思的图收藏进你的表情库（要写备注，别频繁收）", "collect_sticker"))
    if on("vision") and send_tools:
        specs.append(("view_image", [],
                      "看当前消息里的图片（她真的能看到图再说话）；消息里带 [图片] 时优先用它", "view_image"))
    if on("sticker_view") and send_tools:
        specs.append(("view_sticker",
                      [{"type": "string", "name": "sticker_id", "description": "【必填】表情 id（list_stickers 查）"}],
                      "看一张收藏表情的图（不确定某张是什么内容时用，别瞎发）", "view_sticker"))
    if on("sticker_note") and send_tools:
        specs.append(("sticker_note",
                      [{"type": "string", "name": "sticker_id", "description": "【必填】表情 id"},
                       {"type": "string", "name": "note", "description": "【必填】一句备注（≤40字，帮以后认图）"}],
                      "给收藏表情写/改备注（看过图之后写，或群友告诉你这张是什么）", "sticker_note"))
    if on("wake") and send_tools:
        specs.append(("schedule_wake",
                      [{"type": "string", "name": "after_sec",
                        "description": "【必填】多少秒后（5~7200，300=5分钟）"},
                       {"type": "string", "name": "say",
                        "description": "【必填】到时要做的事：普通话说一句；或写 [拍一拍] 到点拍对方一下；"
                                       "或写 [表情:id] 到点发那张收藏表情（可带文字）"},
                       {"type": "string", "name": "reason", "description": "可选：为什么定这个（只给自己看）"},
                       {"type": "string", "name": "mode",
                        "description": "say=到点直接发你写的（省钱）；think=到点先自己想一想再发；不填默认：写了 say 就 say，没写就 think"},
                       {"type": "string", "name": "every_sec",
                        "description": "可选：每多少秒重复一次（≥60，比如 600=每十分钟；不填=只做一次）"}],
                      "定时叫醒自己：对方说“X 秒后/过一会儿/等我回来再说/叫我一下”时，用它把话定到那个时间点"
                      "（到点自动发出，别现在就说）；也可以自己想“过会儿再冒泡”时用",
                      "schedule_wake"))
    if on("wake") and send_tools:
        specs.append(("cancel_wake",
                      [{"type": "string", "name": "scope", "description": "写 all 取消这个会话的全部定时"}],
                      "取消之前定的定时（对方说“别提醒了/取消”时用）", "cancel_wake"))
    if on("face") and send_tools:
        specs.append(("send_face",
                      [{"type": "string", "name": "name",
                        "description": "【必填】官方表情名，比如 汪汪/吃糖/得意/微笑/尊嘟假嘟（见清单）"}],
                      "发一个 QQ 自带小表情（像真人那样偶尔用，别每条都发）", "send_face"))
    if on("poke") and send_tools:
        specs.append(("send_poke",
                      [{"type": "string", "name": "target_id",
                        "description": "戳谁的 QQ 号；留空=戳当前跟你说话的人（私聊里通常留空就行）"}],
                      "拍一拍对方（偶尔逗熟人用，别频繁）", "send_poke"))
    if on("memory_write"):
        specs.append(("memory_append",
                      [{"type": "string", "name": "text", "description": "【必填】要记的一句话（≤120字）"},
                       {"type": "string", "name": "kind",
                        "description": "impression=对某人的印象 / topic=没聊完的话题 / thought=想说的话"}],
                      "把值得记住的东西写进自己的记忆（偶尔用，别当流水账）", "memory_append"))
    if on("finish"):
        specs.append(("finish",
                      [{"type": "string", "name": "reason",
                        "description": "可选：为什么这轮不发言（只写给自己看）"}],
                      "结束本轮：只在你决定这条不发言时才调；正常聊天不要调它", "finish"))
    return specs
