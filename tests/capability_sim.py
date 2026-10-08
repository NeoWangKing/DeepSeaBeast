"""离线验证：她不能假装进语音频道/打游戏，也不能编具体数字；
这类"承诺"不该被记成待办再催她补（2026-10-08 真事故：来打 CS → 来了来了 频道号 1456 直接进）。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/capability_sim.py
"""
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


def build(chat_key):
    import promptlib
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    return promptlib.build_system_prompt(plugin_dir=ROOT, cfg=cfg, chat_key=chat_key,
                                         private=False,
                                         caps={"vision": False, "search": True, "kb": True})[0]


print("== 提示词里有【你做不到的事】 ==")
for _g, _nm in (("869622030", "闪电群"), ("966812151", "豹群"), ("1105410423", "Denial")):
    txt = build(_g)
    ck("%s 有这段" % _nm, "【你做不到的事" in txt)
    ck("%s 写明进不了语音/打不了游戏" % _nm, "进不了语音频道" in txt and "打不了游戏" in txt)
ck("点名禁止「来了来了/频道号 XXXX」", "频道号 XXXX 直接进" in build("869622030"))
ck("禁止编具体数字（频道号/比分/价格）", "不许编具体数字" in build("869622030"))
ck("写明 QQ 里只是个账号", "在 QQ 里只是个账号" in build("869622030"))

print("== 做不到的承诺不再算待办 ==")
from agent import websearch as W          # noqa: E402
cases = [
    ("来 等我一下", "来打cs", False, "来打 CS 的语境"),
    ("我来了，频道号多少", "有人来频道吗", False, "有人在喊来频道"),
    ("我这就进", "频道号多少", False, "说要进频道"),
    ("来了来了 频道号 1456 直接进", "", False, "已经编出来了"),
    ("我上号了", "开黑吗", False, "说要上号"),
    ("我这就去看看", "", True, "正常的「我去看看」"),
    ("等我一下，我去查查", "这比赛谁赢的", True, "查资料的承诺"),
    ("我回头告诉你", "它支持哪些特性", True, "回头告诉你"),
]
for t, c, want, why in cases:
    got = W.is_dangling_promise(t, c)
    ck("%-22s ctx=%-10s → %s（%s）" % (t[:20], c[:8] or "无", got, why), got == want)

print("== 真跑：_note_promise 不再为这种事记待办 ==")
try:
    import main
    from astrbot.api.message_components import Plain
except Exception as e:
    print("  （跳过：%s）" % str(e)[:70])
    print()
    sys.exit(1 if FAIL else 0)


class Ev:
    def __init__(self, text, last="", gid="869622030"):
        self.message_str = text
        self.unified_msg_origin = "qq:GroupMessage:" + gid
        self._gid, self._last = gid, last
        self.sent = []

    def get_group_id(self): return self._gid
    def get_sender_id(self): return "3245938285"
    def get_self_id(self): return "3237702352"
    def get_sender_name(self): return "Neo武神"
    def get_messages(self): return []
    def get_message_type(self): return 1

    def get_result(self):
        ev = self

        class R:
            chain = [Plain(ev._last)]
        return R()

    def set_extra(self, k, v): pass
    def get_extra(self, k, d=None): return d

    async def send(self, chain):
        comps = getattr(chain, "chain", None) or list(chain or [])
        self.sent.append(comps)


o = main.QqPeakGate.__new__(main.QqPeakGate)
o.cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
o.recent = {}
o._log = lambda *a, **k: None
o._log_debug = lambda *a, **k: None
o._fault = lambda *a, **k: None
_saved = []
o._pending_set = lambda key, q, p: _saved.append((key, q, p))
o._pending_wake = lambda *a, **k: None
o._promise_delay = lambda t: 90

o._note_promise(Ev("来打cs", "来 等我一下"))
ck("「来打cs」+「来 等我一下」→ 不记待办", not _saved, str(_saved))
o._note_promise(Ev("有人来频道吗", "我来了，频道号多少"))
ck("喊来频道 +「我来了」→ 不记待办", not _saved, str(_saved))
o._note_promise(Ev("这比赛谁赢的呀", "我这就去看看"))
ck("正常查资料承诺 → 照旧记待办", len(_saved) == 1, str(_saved))

_pp = os.path.join(ROOT, "data", "agent_pending.json")
try:
    _d = json.load(open(_pp, encoding="utf-8"))
    _bad = [k for k, v in _d.items() if "频道" in str((v or {}).get("q") or "")]
    ck("旧的那条待办已清掉", not _bad, str(_bad))
except Exception:
    ck("旧的那条待办已清掉（文件不存在=已清）", True)


print("== 发送口兜底：假装进了语音/开了游戏 → 不发 ==")
import replyproto as R          # noqa: E402
for _t, _want, _why in [
        ("来了来了 频道号 1456 直接进", True, "编了频道号"),
        ("我来了，频道号多少", True, "问频道号"),
        ("频道还开着吗，我这就进", True, "说要进频道"),
        ("我上号了", True, "说上号"),
        ("我进语音了", True, "说进语音"),
        ("我这就进", True, "短句说要进"),
        ("我进不了语音呀，你们打，我在这儿喊 666", False, "老实说去不了（必须放行）"),
        ("我打不了游戏，只能在这儿陪你们聊", False, "老实说打不了"),
        ("我这就去看看那个仓库", False, "看代码的承诺"),
        ("这个仓库我翻过了，文档写得挺清楚", False, "正常聊代码")]:
    _got = R.looks_like_fake_deed(_t)
    ck("%-30s → %s（%s）" % (_t[:28], _got, _why), _got == _want)

import asyncio                  # noqa: E402
_ev2 = Ev("来打cs")
_tgt2 = o._agent_target(_ev2)
asyncio.run(o._agent_send(_tgt2, "来了来了 频道号 1456 直接进"))
ck("这种话真的没发出去", not _ev2.sent, str(_ev2.sent)[:60])
asyncio.run(o._agent_send(_tgt2, "我进不了语音呀，你们打，我在这儿喊 666"))
ck("老实说去不了的话照常发", bool(_ev2.sent), str(_ev2.sent)[:60])

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
