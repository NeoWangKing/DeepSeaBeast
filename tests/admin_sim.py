"""离线验证：主人私聊里的只读后台 —— 只认主人、只读、不误伤正常聊天。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/admin_sim.py
"""
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


_cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
from agent import admin as A            # noqa: E402

print("== 配置 ==")
_ic = _cfg.get("admin") or {}
ck("admin.enabled", _ic.get("enabled") is True)
ck("只认主人 3245938285", _ic.get("owner_ids") == ["3245938285"])
ck("群备注有名字（好认）", "869622030" in (_ic.get("group_notes") or {}))

print("== 命令识别（他例子的说法要认） ==")
for q, want in [("列出一下你现在的所有人格面具", True), ("人格面具", True),
                ("人格卡都有哪些", True), ("群列表", True), ("看看有哪些群", True),
                ("记忆", True), ("记忆 869622030", True), ("表情包", True),
                ("资料库", True), ("待办", True), ("技能", True), ("模型", True),
                ("参数 agent.max_rounds", True), ("配置 stickers", True),
                ("后台", True), ("能看什么", True)]:
    h, _p = A.route(q, _cfg, ROOT)
    ck("%-24s → 认" % q, h is True)

print("== 正常聊天不能被吃掉 ==")
for q in ("这群人真多", "今天晚饭吃什么", "我去洗个澡", "顺便帮我看看群里都聊啥",
          "你怎么看这个事", "哈哈哈哈哈", "我发你个表情包好不好笑？"[:12], "晚安",
          "我记忆里好像没有这个人", "刚才那个群的问题你怎么不回"):
    h, _p = A.route(q, _cfg, ROOT)
    ck("%-24s → 不认（正常聊天）" % q, h is False)

print("== 只读：不写任何文件 ==")
import time                          # noqa: E402
_watch = ["config.json", "data/agent_active.json", "data/agent_pending.json",
          "data/memory/group_profile/869622030.md", "data/stickers/index.json"]
_before = {f: os.path.getmtime(os.path.join(ROOT, f)) for f in _watch
           if os.path.exists(os.path.join(ROOT, f))}
for q in [p for p, w in [("人格面具", 1), ("群列表", 1), ("记忆 869622030", 1), ("表情包", 1),
                         ("资料库", 1), ("待办", 1), ("技能", 1), ("故障", 1), ("模型", 1),
                         ("总览", 1), ("参数 agent.max_rounds", 1)]]:
    A.route(q, _cfg, ROOT)
time.sleep(0.05)
_after = {f: os.path.getmtime(os.path.join(ROOT, f)) for f in _watch
          if os.path.exists(os.path.join(ROOT, f))}
ck("跑完一圈后这些文件都没被改过", _before == _after,
   str([k for k in _before if _before[k] != _after.get(k)]))

print("== 回复长度符合 QQ 单条限制 ==")
for q in ("人格面具", "群列表", "记忆", "表情包", "资料库", "总览"):
    _h, parts = A.route(q, _cfg, ROOT)
    ck("%-8s ≤4 条且每条 ≤300 字" % q, parts and len(parts) <= 4
       and all(len(x) <= 300 for x in parts), "条数=%d 最长=%d"
       % (len(parts), max(len(x) for x in parts)))
    ck("%-8s 带【后台·只读】头" % q, "【后台·只读｜" in parts[0])

print("== 真跑：只认主人 + 只在私聊 ==")
try:
    import main
    from astrbot.api.message_components import Plain
except Exception as e:
    print("  （跳过：%s）" % str(e)[:70])
    print()
    sys.exit(1 if FAIL else 0)


class Ev:
    def __init__(self, text, gid="", uid="3245938285"):
        self.message_str = text
        self.unified_msg_origin = "qq:PrivateMessage:" + uid
        self._gid, self._uid, self.sent = gid, uid, []
        self.raw_message = {}

    def get_group_id(self): return self._gid
    def get_sender_id(self): return self._uid
    def get_self_id(self): return "3237702352"
    def get_sender_name(self): return "灵其啊"
    def get_messages(self): return []
    def get_message_type(self): return 2
    def set_extra(self, k, v): pass
    def get_extra(self, k, d=None): return d

    def get_result(self):
        class R: chain = []
        return R()

    async def send(self, chain):
        comps = getattr(chain, "chain", None) or list(chain or [])
        self.sent.append("".join(str(getattr(c, "text", "")) for c in comps
                                 if isinstance(c, Plain)))


def _plug():
    o = main.QqPeakGate.__new__(main.QqPeakGate)
    o.cfg = _cfg
    o.recent = {}
    o.last_reply_ts, o.last_reply_to = {}, {}
    o._log = lambda *a, **k: None
    o._log_debug = lambda *a, **k: None
    o._fault = lambda *a, **k: None
    return o


o = _plug()
ev = Ev("列出一下你现在的所有人格面具")
ck("主人私聊说这句 → 接管", asyncio.run(o._admin_console(ev)) is True)
ck("…真的回出来了", bool(ev.sent) and "后台·只读" in ev.sent[0], str(ev.sent)[:70])

ev2 = Ev("列出一下你现在的所有人格面具", uid="287383512")
ck("别人私聊 → 完全不接管（正常聊天）", asyncio.run(o._admin_console(ev2)) is False)
ck("…一个字都没发", not ev2.sent)

ev3 = Ev("人格面具", gid="869622030")
ck("群里 → 不接管", asyncio.run(o._admin_console(ev3)) is False)

ev4 = Ev("今天吃什么好呢")
ck("主人私聊闲聊 → 不接管", asyncio.run(o._admin_console(ev4)) is False)

_cfg2 = json.loads(json.dumps(_cfg))
_cfg2["admin"]["enabled"] = False
o2 = _plug()
o2.cfg = _cfg2
ev5 = Ev("人格面具")
ck("admin.enabled=false → 整个后台关掉", asyncio.run(o2._admin_console(ev5)) is False)

_cfg3 = json.loads(json.dumps(_cfg))
_cfg3["admin"].pop("owner_ids", None)
o3 = _plug()
o3.cfg = _cfg3
ev6 = Ev("人格面具")
ck("没配 admin.owner_ids → 退回 activation.owner_ids（还是认主人）",
   asyncio.run(o3._admin_console(ev6)) is True)

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
