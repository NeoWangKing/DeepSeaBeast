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

    # ---------- 收尾类 ----------
    def finish(self, reason="") -> str:
        self.finished = True
        self.finish_reason = str(reason or "")[:80]
        return "本轮结束（没有要发出的内容）"

    # ---------- 状态 ----------
    @property
    def spoke(self) -> bool:
        return bool(self.sent)

    def state(self) -> dict:
        return {"chat_key": self.chat_key, "sent": list(self.sent), "finished": self.finished,
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
                        "description": "要说的话；想分成多条就用 ||| 分隔（最多 4 条、总共 400 字内）"},
                       {"type": "string", "name": "reply_to_id", "description": "可选：要引用哪条消息的 id"}],
                      "在群里发言（你写的正文不会被发出去，发言必须用这个工具）", "send_message"))
    if on("sticker") and send_tools:
        specs.append(("send_sticker",
                      [{"type": "string", "name": "sticker_id", "description": "表情 id（用 list_stickers 查）"}],
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
                      [{"type": "string", "name": "message_id", "description": "那条消息的 id"},
                       {"type": "string", "name": "note", "description": "一句简短备注（以后靠它认图）"}],
                      "把群友刚发的有意思的图收藏进你的表情库（要写备注，别频繁收）", "collect_sticker"))
    if on("finish"):
        specs.append(("finish",
                      [{"type": "string", "name": "reason",
                        "description": "可选：为什么这轮不发言（只写给自己看）"}],
                      "结束本轮：想潜水、或者没什么要说的，就调用它", "finish"))
    return specs
