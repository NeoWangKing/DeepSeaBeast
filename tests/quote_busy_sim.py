"""离线验证：多人同时说话 → 自动引用当前这条（且一轮只引用一次）。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/quote_busy_sim.py
"""
import asyncio
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import main                   # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
GID = "869622030"
NOW = time.time()


def mk(recent, last_reply_ts=0, uid="u2", mid=9001):
    o = main.QqPeakGate.__new__(main.QqPeakGate)
    o.cfg = cfg
    o.recent = {GID: list(recent)}
    o.last_reply_ts = {GID: last_reply_ts}
    o.last_reply_to = {GID: "u1"}
    o.last_msg = {GID: (str(mid), NOW)}
    o._log = lambda *a, **k: None
    o._log_debug = lambda *a, **k: None

    class Ev:
        message_str = "在吗"
        unified_msg_origin = "qq:GroupMessage:%s" % GID

        class message_obj:
            message_id = mid

        def get_group_id(self): return GID
        def get_sender_id(self): return uid
        def get_self_id(self): return "3237702352"
        def get_sender_name(self): return "某人"
        def get_messages(self): return []
        def get_message_type(self): return 1
        def set_extra(self, k, v): pass
        def get_extra(self, k, d=None): return d
        def stop_event(self): pass

    return o, Ev()


print("== 忙的时候要引用 ==")
_o, _ev = mk([("阿强", "在吗", "u1", NOW - 30), ("小美", "问你个事", "u2", NOW - 20),
              ("阿强", "在吗在吗", "u1", NOW - 10)], last_reply_ts=NOW - 40)
ck("2 条以上 + 2 人说话 → 引用", _o._quote_reason(_ev) == "busy", _o._quote_reason(_ev))
ck("… _quote_id 给出当前消息 id", _o._quote_id(_ev) == "9001", _o._quote_id(_ev))

_o, _ev = mk([("小美", "喂", "u2", NOW - 8), ("小美", "喂喂", "u2", NOW - 5)],
             last_reply_ts=NOW - 20)
ck("同一人连发 2 条 → 引用（会被挤上去）", _o._quote_id(_ev) == "9001")

print("== 不该引用的时候别乱引用 ==")
_o, _ev = mk([], last_reply_ts=NOW - 20)
ck("她说过话后没人再说话 → 不引用", _o._quote_id(_ev) == "", _o._quote_reason(_ev))
_o, _ev = mk([("小美", "在吗", "u2", NOW - 5)], last_reply_ts=NOW - 20)
ck("只有 1 条、1 个人 → 不引用（免得每条都挂引用）", _o._quote_id(_ev) == "",
   _o._quote_reason(_ev))
_o, _ev = mk([("小美", "在吗", "u2", NOW - 5), ("阿强", "嗯", "u1", NOW - 4)],
             last_reply_ts=NOW - 20)
ck("2 条但都是…（2 人）→ 仍引用", _o._quote_id(_ev) == "9001")
_cfg2 = json.loads(json.dumps(cfg))
_cfg2["quote_when_busy"] = False
_o, _ev = mk([("阿强", "在吗", "u1", NOW - 30), ("小美", "问你个事", "u2", NOW - 20)],
             last_reply_ts=NOW - 40)
_o.cfg = _cfg2
ck("关掉 quote_when_busy → 不引用", _o._quote_id(_ev) == "")

print("== agent 工具路径：一轮只引用第一条 ==")


async def _run():
    o, ev = mk([("阿强", "在吗", "u1", NOW - 30), ("小美", "问你个事", "u2", NOW - 20)],
               last_reply_ts=NOW - 40)
    sent = []

    async def _send(target, text, reply_to_id="", at_user_id="", face=""):
        sent.append((text, reply_to_id))
    o._agent_send = _send

    async def _stick(target, it, reply_to_id=""):
        sent.append(("STICKER", reply_to_id))
    o._agent_send_sticker = _stick
    cb = o._agent_callbacks({"gid": GID, "uid": "u2",
                             "umo": "qq:GroupMessage:%s" % GID, "event": ev})
    cb["send_text"]("第一句")
    cb["send_text"]("第二句")
    await asyncio.sleep(0.15)
    return sent


_sent = asyncio.run(_run())
ck("第一条带引用", _sent and _sent[0][1] == "9001", str(_sent))
ck("第二条不带引用（一轮只引一次）", len(_sent) > 1 and _sent[1][1] == "", str(_sent))

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
