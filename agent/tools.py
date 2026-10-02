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
        self.errors = []

    # ---------- 发送类 ----------
    def _split(self, text: str) -> list:
        raw = str(text or "")
        parts = [x.strip() for x in raw.replace("｜", "|").split("|||")]
        parts = [x for x in parts if x]
        if not parts and raw.strip():
            parts = [raw.strip()]
        return parts[:MAX_PARTS]

    def send_message(self, text="", reply_to_id="", at_user_id="") -> str:
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
        for p in parts:
            try:
                fn(p, reply_to_id=str(reply_to_id or ""), at_user_id=str(at_user_id or ""))
                self.sent.append(("text", p))
                ok += 1
            except Exception as e:
                self.errors.append("send_message: %r" % (e,))
                return "发了 %d 条后出错：%r" % (ok, e)
        return "已发出 %d 条" % ok

    def send_sticker(self, sticker_id="", reply_to_id="") -> str:
        sid = str(sticker_id or "").strip()
        if not sid:
            return "没发：要先给 sticker_id（可以用 list_stickers 查）"
        fn = self.cb.get("send_sticker")
        if not fn:
            return "没发：这个会话现在不能发表情"
        try:
            r = fn(sid, reply_to_id=str(reply_to_id or ""))
        except Exception as e:
            self.errors.append("send_sticker: %r" % (e,))
            return "没发：%r" % (e,)
        if not r:
            return "没发：没找到 id=%s 的表情（用 list_stickers 查）" % sid
        self.sent.append(("sticker", sid))
        return "已发出表情 %s" % sid

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
                       {"type": "string", "name": "reply_to_id", "description": "可选：要引用哪条消息的 id"}],
                      "发言：你写的正文永远不会发出去，说话只能调这个工具", "send_message"))
    if on("sticker") and send_tools:
        specs.append(("send_sticker",
                      [{"type": "string", "name": "sticker_id", "description": "【必填】表情 id（用 list_stickers 查）"}],
                      "发一张指定 id 的收藏表情（一条消息只能一张）", "send_sticker"))
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
