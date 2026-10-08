"""离线验证：群激活（新群默认哑、主人 @ 激活、主人说「关机」退回）＋接线。

需要 astrbot 运行环境的部分会自动跳过（用插件 python 跑就全跑）：
    /opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/activation_sim.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from agent import activation as A      # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


print("== 「关机」命令识别（短命令才算） ==")
for t in ["关机", "@蓝色邪恶大肥鱼 关机", "@小鲸鱼 关闭。", "先退下", "  下线 ", "闭嘴！"]:
    ck("认得出 %r" % t, A.is_poweroff(t))
for t in ["这台机器怎么关机", "关机以后怎么办", "怎么关机呢", "我在摸鱼", "", "@你 帮我查下关机流程"]:
    ck("不误判 %r" % t, not A.is_poweroff(t))
ck("开机命令识别", A.is_on_command("开机") and A.is_on_command("@她 上线"))
ck("strip_at 去掉 @昵称(QQ)", A.strip_at("@小鲸鱼(3237702352) 关机") == "关机")

print("== 状态读写 ==")
_s = A.merge_state({}, "123", True, "3245938285")
ck("激活后 is_active", A.is_active(_s, "123"))
ck("没记录 = 未激活", not A.is_active({}, "123"))
_s2 = A.merge_state(_s, "123", False, "3245938285")
ck("关机后 is_active 为假", not A.is_active(_s2, "123"))
ck("seed_from 批量置为已激活", len(A.seed_from(["1", "2"]).get("groups")) == 2)

print("== main.py 接线 ==")
_src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("有状态机 _group_ready", "async def _group_ready(" in _src)
ck("gate 最前面拦一道", "群激活：没激活的群什么都不做" in _src)
ck("唤醒也守（未激活不主动说）", "定时唤醒：群 %s 未激活 → 跳过" in _src)
ck("有 owner 判定", "def _is_owner(" in _src)
_cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
_act = _cfg.get("activation") or {}
ck("配置有 owner_ids", str(_cfg.get("activation", {}).get("owner_ids")) == "['3245938285']", _act.get("owner_ids"))
ck("配置有 off_lines", bool(_act.get("off_lines")))

print("== 真跑一遍状态机（需要 astrbot 运行环境） ==")
try:
    import main
    from astrbot.api.message_components import At, Plain
except Exception as e:
    print("  （跳过：%s）" % str(e)[:60])
else:
    class Ev:
        def __init__(self, text, gid="777888999", uid="3245938285", at=True):
            self.message_str = text
            self.unified_msg_origin = "qq:GroupMessage:" + gid
            self._gid, self._uid, self._at, self.sent, self._extra = gid, uid, at, [], {}
            self.raw_message = {}
        def get_group_id(self): return self._gid
        def get_sender_id(self): return self._uid
        def get_self_id(self): return "3237702352"
        def get_sender_name(self): return "Neo武神"
        def get_messages(self):
            return [At(qq="3237702352")] if self._at else []
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

    tmp = tempfile.mkdtemp(prefix="act-")
    o = main.QqPeakGate.__new__(main.QqPeakGate)
    o.cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    o._log = lambda *a, **k: print("   [log]", *a)
    o._log_debug = lambda *a, **k: None
    o._active_path = lambda: os.path.join(tmp, "agent_active.json")
    import asyncio
    _e1 = Ev("@你 帮我看下这个 bug", uid="999", at=True)          # 别人 @ 她（群没激活）
    ck("未激活群 + 非主人 @ → 忽略", asyncio.run(o._group_ready("777888999", _e1, _e1.message_str)) is False)
    _e2 = Ev("随便聊聊", uid="999", at=False)
    ck("未激活群 + 别人闲聊 → 忽略", asyncio.run(o._group_ready("777888999", _e2, _e2.message_str)) is False)
    _e3 = Ev("@你 帮我看看这个函数", uid="3245938285", at=True)     # 主人 @ → 激活
    ck("未激活群 + 主人 @ → 激活并放行", asyncio.run(o._group_ready("777888999", _e3, _e3.message_str)) is True)
    ck("状态已写入（已激活）", o._group_active("777888999"))
    _e4 = Ev("来 @你 关机", uid="3245938285", at=True)             # 主人说关机
    ck("主人说关机 → 吞掉这条", asyncio.run(o._group_ready("777888999", _e4, "关机")) is False)
    ck("状态已写回未激活", not o._group_active("777888999"))
    ck("回了一句关机确认", bool([x for x in _e4.sent if x.strip()]), _e4.sent)
    _e5 = Ev("又来闲聊", uid="999", at=False)
    ck("再回到未激活：闲聊仍被忽略", asyncio.run(o._group_ready("777888999", _e5, _e5.message_str)) is False)
    _e6 = Ev("", uid="3245938285", at=True)
    ck("主人再 @ → 又能激活", asyncio.run(o._group_ready("777888999", _e6, "")) is True)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

print()
if FAIL:
    print("有问题：%s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
