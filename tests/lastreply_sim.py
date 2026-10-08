"""离线+真跑：她通过工具轮说过话后，last_reply_ts / last_reply_to 必须更新
（否则「对话延续」「冷却」「接话窗口」全部失效——2026-10-08 的真实事故）。

用插件 python 跑才会执行真跑部分：
    /opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/lastreply_sim.py
"""
import asyncio, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


_src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
print("== 接线 ==")
ck("工具轮发文字后更新时间戳", "她刚说过话：冷却 / 对话延续 / 接话窗口都要靠这两个戳" in _src)
ck("发表情也算她说过话", '发表情也算"她刚说过话"' in _src)

print("== 真跑（需要 astrbot 运行环境） ==")
try:
    import main
    from astrbot.api.message_components import Plain
except Exception as e:
    print("  （跳过：%s）" % str(e)[:60])
    sys.exit(0 if not FAIL else 1)


class Ev:
    def __init__(self, text="", gid="869622030", uid="3245938285"):
        self.message_str = text
        self.unified_msg_origin = "qq:GroupMessage:" + gid
        self._gid, self._uid, self.sent, self._extra = gid, uid, [], {}
        self.raw_message = {}

    def get_group_id(self): return self._gid
    def get_sender_id(self): return self._uid
    def get_self_id(self): return "3237702352"
    def get_sender_name(self): return "Neo武神"
    def get_messages(self): return []
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
o.recent = {}
o.last_reply_ts, o.last_reply_to = {}, {}
o._log = lambda *a, **k: None
o._log_debug = lambda *a, **k: None
o._fault = lambda *a, **k: None
ev = Ev("@你 在吗")
tgt = o._agent_target(ev)
asyncio.run(o._agent_send(tgt, "在的"))
ck("last_reply_ts 有值了", float(o.last_reply_ts.get("869622030") or 0) > 0,
   o.last_reply_ts.get("869622030"))
ck("last_reply_to 记的是发消息的人", str(o.last_reply_to.get("869622030")) == "3245938285",
   o.last_reply_to.get("869622030"))
ck("时间戳是刚刚（10 秒内）", time.time() - float(o.last_reply_ts.get("869622030") or 0) < 10)
# 用它跑一次"对话延续"判断（真实场景：对方隔十几秒接着说 → 该放行）
from scoring import cont_pass
_now = time.time()
ck("刚发完 1 秒内不接（防连刷）",
   not cont_pass(_now, _now - 1, "3245938285", "3245938285", False, False))
ck("隔 14 秒同一个人接着说 → 对话延续放行",
   cont_pass(_now, _now - 14, "3245938285", "3245938285", False, False))
ck("用的是刚写进去的 last_reply_to",
   cont_pass(_now, _now - 14, "3245938285", o.last_reply_to.get("869622030"), False, False))

print()
if FAIL:
    print("有问题：%s" % "、".join(FAIL)); sys.exit(1)
print("全部通过")
