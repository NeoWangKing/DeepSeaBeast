"""离线验证：① 引用消息里的图会被取回来 ② 只@群里主人手打的 @名字也算叫她。"""
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


import main                                  # noqa: E402
from astrbot.api.message_components import Plain  # noqa: E402

_src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()

print("== 接线 ==")
ck("有 _reply_id 辅助", "def _reply_id(event)" in _src)
ck("会用 get_msg 取引用消息", 'call_action("get_msg"' in _src)
ck("引用里的图会进 _img_paths", "_img_paths.append(_p2)" in _src)
ck("被引用的文字会带进上下文", "【他引用的那条消息】" in _src)
ck("只@群识别主人手打的 @名字", "_called_txt" in _src and "主人手打的 @名字" in _src)


class _Reply:
    def __init__(self, i):
        self.id = str(i)
        self.type = "reply"


class _Msg:
    def __init__(self, i):
        self.id = str(i)
        self.type = "reply"


class Ev:
    def __init__(self, text="", gid="1105410423", uid="3245938285", comps=None, raw=None, qmsg=None):
        self.message_str = text
        self._gid, self._uid = str(gid), str(uid)
        self._comps = list(comps or [])
        self.raw_message = raw or {}
        self._qmsg = qmsg
        self.calls = []
        self.sent = []
        self.stopped = False
        self.allowed = False

        class _Bot:
            def __init__(self, outer):
                self.o = outer

            async def call_action(self, action, **kw):
                self.o.calls.append((action, kw))
                if action == "get_msg":
                    return {"message": self.o._qmsg or []}
                return {}

        self.bot = _Bot(self)

    def get_group_id(self): return self._gid
    def get_sender_id(self): return self._uid
    def get_self_id(self): return "3237702352"
    def get_sender_name(self): return "灵其啊"
    def get_messages(self): return self._comps
    def get_message_type(self): return 1

    def get_result(self):
        class R: chain = []
        return R()

    def set_extra(self, k, v): pass
    def get_extra(self, k, d=None): return d
    def stop_event(self):
        self.stopped = True
    def is_at_or_wake_command(self): return False
    def is_stopped(self): return bool(getattr(self, "stopped", False))
    @property
    def unified_msg_origin(self): return "qq:GroupMessage:%s" % self._gid
    def get_self_id_extra(self): return self.get_self_id()

    async def send(self, chain):
        comps = getattr(chain, "chain", None) or list(chain or [])
        self.sent.append("".join(str(getattr(c, "text", "")) for c in comps
                                 if isinstance(c, Plain)))


o = main.QqPeakGate.__new__(main.QqPeakGate)
o.cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
o._nick = ""
o._log = lambda *a, **k: None
o._log_debug = lambda *a, **k: None

print("== _reply_id 识别引用 ==")
ck("组件里是 Reply", main.QqPeakGate._reply_id(Ev(comps=[_Reply(805307318)])) == "805307318")
ck("原始报文里是 reply",
   main.QqPeakGate._reply_id(Ev(raw={"message": [{"type": "reply", "data": {"id": "999"}}]})) == "999")
ck("没有引用返回空", main.QqPeakGate._reply_id(Ev()) == "")

print("== 只@群：主人手打 @名字 ==")
_cfg2 = json.loads(json.dumps(o.cfg))
o2 = main.QqPeakGate.__new__(main.QqPeakGate)
o2.cfg = _cfg2
o2._nick = ""
o2._log = lambda *a, **k: None
o2._log_debug = lambda *a, **k: None
o2.scorer = None
o2.hour_count, o2.last_reply_ts, o2.last_auto, o2.engaged_streak = {}, {}, {}, {}
o2.recent, o2.sender_times, o2.msg_times, o2.prev_speaker = {}, {}, {}, {}
o2.sender_text, o2.last_reply_to, o2._t_in = {}, {}, {}
o2._extra = lambda *a, **k: None


def _allow_flag(ev):
    """跑一遍 gate：True = 走到 _allow（她会回）；False = 被吞掉。"""
    async def _run():
        o2._agent_loop_on = lambda e: False
        o2._allow = _fake_allow
        o2._reply_delay = lambda *a, **k: 0.0          # 关掉拟人延迟（免得住 defer 分支）
        # 桩掉 gate 前半段会碰到的插件方法（它们需要真实运行环境）
        for _f in ("_remember", "_touch", "_log_chat", "_note_sender", "_bot_nickname",
                   "_poked_me", "_at_other", "_quotes_me", "_face_menu_text",
                   "_remember_bot_line", "_ensure_group_card", "_turtle_enter"):
            setattr(o2, _f, (lambda *a, **k: None))
        o2._in = lambda *a, **k: False
        o2._tune_i = lambda *a, **k: 99
        o2._tune_f = lambda *a, **k: 0.0
        o2._promise_delay = lambda *a, **k: 90
        o2._group_active = lambda *a, **k: True
        async def _nn(e):
            return ""
        o2._bot_nickname = _nn                        # 免得住 API 调用
        await main.QqPeakGate.gate(o2, ev)
        return bool(getattr(ev, "allowed", False))

    async def _fake_allow(e):
        e.allowed = True
        e.stop_event()

    return asyncio.run(_run())


def _mk(text, uid="3245938285", ats=()):
    ev = Ev(text=text, uid=uid)
    if ats:
        class _At:
            def __init__(self, q):
                self.qq = q
                self.type = "at"
        ev._comps = [_At(q) for q in ats]
    return ev


_ev = _mk("@大肥鱼 你能看到这张图片里面的报错信息吗")
_ev.stopped = False
_ok1 = _allow_flag(_ev)
ck("默认：手打 @ 不算（只认真 At）", _ok1 is False, str(_ok1))

_evx = _mk("大肥鱼回我了")                     # 提到名字但不是 @
_evx.stopped = False
ck("默认：提到名字不算", _allow_flag(_evx) is False)

o2.cfg["only_at_allow_text_at"] = True          # 打开宽松开关
_ev3 = _mk("@大肥鱼 看下这张图")
_ev3.stopped = False
ck("开关打开后：句首 @名字 才算", _allow_flag(_ev3) is True)
_ev4 = _mk("大肥鱼回我了")
_ev4.stopped = False
ck("开关打开后：句中提名字仍不算", _allow_flag(_ev4) is False)
o2.cfg.pop("only_at_allow_text_at", None)

_ev2 = _mk("大肥鱼这个功能不错", uid="1146112953")     # 别人 + 名字出现在句中
_ev2.stopped = False
_ok2 = _allow_flag(_ev2)
ck("别人句中提到名字 → 仍然不放行（只认@）", _ok2 is False, str(_ok2))

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
