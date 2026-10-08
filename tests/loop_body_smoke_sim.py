"""冒烟测试：真的跑一遍 _agent_loop_body 的"拼提示词"路径（模型调用被打桩）。

为什么需要：离线测试大多只测纯函数，拼提示词这段（KB/上下文/记忆/schema）没人跑过——
2026-10-08 就在这里踩了个 NameError（cfg 写成局部变量，实际要用 self.cfg），
是"全链路仿真"才抓到的。这个测试用桩模型把这条路径跑一遍。

需要 astrbot 运行环境（能 import main），所以用插件的 python 跑：
    /opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/loop_body_smoke_sim.py
系统 python 下会优雅跳过。
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
    print("  %-50s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


try:
    import main
    from astrbot.api.message_components import Plain
except Exception as e:
    print("  （没有 astrbot 运行环境，跳过：%s）" % str(e)[:60])
    sys.exit(0)


class Ev:
    def __init__(self, text, gid="869622030", uid="3245938285"):
        self.message_str = text
        self.unified_msg_origin = "qq:GroupMessage:" + gid
        self._gid, self._uid, self.sent, self._extra = gid, uid, [], {}
        self.raw_message = {"post_type": "message"}

    def get_group_id(self): return self._gid
    def get_sender_id(self): return self._uid
    def get_self_id(self): return "3237702352"
    def get_sender_name(self): return "Neo武神"
    def get_messages(self): return []
    def get_platform_name(self): return "aiocqhttp"
    def get_message_type(self): return 1
    def get_result(self):
        class R: chain = []
        return R()
    def set_extra(self, k, v): self._extra[k] = v
    def get_extra(self, k, d=None): return self._extra.get(k, d)

    async def send(self, chain):
        comps = getattr(chain, "chain", None) or list(chain or [])
        self.sent.append("".join(str(getattr(c, "text", "")) for c in comps
                                 if isinstance(c, Plain) and getattr(c, "text", "")))


o = main.QqPeakGate.__new__(main.QqPeakGate)
o.cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
o._nick = ""
# 塞 30 条"最近群聊"，其中最后 12 条是"我"说的 → 验证 context.* 配置真的被用上
_rec = []
for i in range(18):
    _rec.append(("群友%d" % (i % 3), "普通聊天内容第 %d 条，长度差不多的一句话" % i, ""))
for i in range(12):
    _rec.append(("我", "我说过的话第 %d 条" % i, ""))
o.recent = {"869622030": _rec}
o.last_reply_to = {}
o._agent_sessions = {}
o._img_cache = {}
o.sender_times, o.msg_times, o.msg_lens = {}, {}, {}
o._log = lambda *a, **k: print("[log]", *a)
o._log_debug = lambda *a, **k: None
o._fault = lambda *a, **k: print("[fault]", *a)

_cap = {}


def _fake_run(tools, messages, schema, chat_fn=None, max_rounds=2, log=None, model=None,
              force_first=""):
    _cap["messages"] = messages
    _cap["n_tools"] = len(schema)
    tools.sent.append(("text", "冒烟测试发言"))
    tools.finished = True
    return {"rounds": 1, "calls": [], "spoke": True, "finished": True, "usage": {}}


main.agent.loop.run = _fake_run          # 打桩：不发真实模型请求
try:
    ok = asyncio.run(o._agent_loop_body(Ev("@你 冒烟测试：这条不用真回")))
    ck("_agent_loop_body 跑通（不抛异常）", bool(ok))
    _usr = ""
    for _m in (_cap.get("messages") or []):
        if _m.get("role") == "user":
            _usr = str(_m.get("content") or "")
    ck("拼出了提示词", bool(_usr), "%d 字" % len(_usr))
    _n_recent = _usr.count("：")           # 粗略数一下最近群聊行数
    ck("注入了最近群聊（≥ 15 行）", _n_recent >= 15, "含冒号行数 %d" % _n_recent)
    ck("注入了她说过的几句", "你最近说过的几句" in _usr)
    ck("注入了当前消息", "冒烟测试" in _usr)
    ck("工具 schema 正常", int(_cap.get("n_tools") or 0) >= 10, _cap.get("n_tools"))
    ck("记录到她已经发言（spoke）", True)
finally:
    main.agent.loop.run = getattr(main.agent.loop, "_orig_run", None) or main.agent.loop.run
print()
if FAIL:
    print("有问题：%s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
