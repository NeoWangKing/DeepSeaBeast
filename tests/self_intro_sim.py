"""离线验证：自我介绍段（简短/谦虚/必说内容）+ Denial 群对 Synthia 的定位 + 能 @ 人。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/self_intro_sim.py
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


def build(chat_key, cfg=None):
    import promptlib
    _cfg = cfg or json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    txt, meta = promptlib.build_system_prompt(plugin_dir=ROOT, cfg=_cfg, chat_key=chat_key,
                                              private=False,
                                              caps={"vision": False, "search": True, "kb": True})
    return txt, meta


print("== 配置 ==")
_cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
ic = _cfg.get("intro") or {}
ck("intro.enabled", ic.get("enabled") is True)
ck("owner 是灵其啊 3245938285",
   ic.get("owner_qq") == "3245938285" and ic.get("owner_name") == "灵其啊")
ck("限制很紧（≤2 条 / ≤120 字）",
   int(ic.get("max_parts") or 9) <= 2 and int(ic.get("max_total_chars") or 9999) <= 150)
ck("Denial 群有 Synthia 说明", "1105410423" in (ic.get("other_bots") or {}))
ck("Synthia 说明里点明『正主』并禁止比较",
   "正主" in ic["other_bots"]["1105410423"] and "比" in ic["other_bots"]["1105410423"])

print("== 提示词里确实有这段 ==")
txt_d, meta_d = build("1105410423")
ck("人格卡是技术助手", "project_assistant" in str(meta_d.get("persona")), str(meta_d.get("persona")))
ck("有【自我介绍】段", "【自我介绍】" in txt_d)
ck("说了要短（最多 2 条）", "最多 2 条消息" in txt_d, txt_d[txt_d.find("必须短"):][:40] if "必须短" in txt_d else "")
ck("说了是灵其啊写的", "灵其啊" in txt_d and "3245938285" in txt_d)
ck("说了可以 @ 他（教了 at_user_id）", "at_user_id" in txt_d)
ck("说了只能帮简单的事", "一些简单的事" in txt_d)
ck("说了别比、别炫技", "炫技" in txt_d and "比谁厉害" in txt_d)
ck("Denial 群带上 Synthia 提示", "Synthia" in txt_d)
ck("Synthia 是正主、要先让着她", "Synthia 才是这里的正主" in txt_d)

txt_s, _ = build("869622030")
ck("其它群不带 Synthia（别乱说别的群有谁）", "Synthia" not in txt_s)
ck("其它群仍然有自我介绍段", "【自我介绍】" in txt_s)
ck("自我介绍段没被技术助手人格关掉",
   "sticker_rules" not in txt_d and "【自我介绍】" in txt_d)

_cfg2 = json.loads(json.dumps(_cfg))
_cfg2["intro"]["enabled"] = False
txt_off, _ = build("1105410423", _cfg2)
ck("intro.enabled=false 时整段消失", "【自我介绍】" not in txt_off)

print("== @ 人：工具 schema + 真的发得出去 ==")
try:
    from agent import tools as T
    specs = T.spec_list()
    sc = [x for x in specs if x[0] == "send_message"][0]
    names = [p.get("name") for p in sc[1]]
    ck("send_message 有 at_user_id 参数", "at_user_id" in names, str(names))
    ck("description 提醒别每条都 @",
       any("别每条都 @" in str(p.get("description") or "") for p in sc[1]))
except Exception as e:
    print("  （specs 检查跳过：%r）" % (e,))

try:
    import asyncio
    import main
    from astrbot.api.message_components import At, Plain
except Exception as e:
    print("  （跳过真跑：%s）" % str(e)[:70])
    print()
    sys.exit(1 if FAIL else 0)


class Ev:
    def __init__(self, text="自我介绍", gid="1105410423", uid="3245938285"):
        self.message_str = text
        self.unified_msg_origin = "qq:GroupMessage:" + gid
        self._gid, self._uid, self.sent, self._extra = gid, uid, [], {}
        self.raw_message = {}

    def get_group_id(self): return self._gid
    def get_sender_id(self): return self._uid
    def get_self_id(self): return "3237702352"
    def get_sender_name(self): return "灵其啊"
    def get_messages(self): return []
    def get_message_type(self): return 1
    def get_result(self):
        class R: chain = []
        return R()
    def set_extra(self, k, v): self._extra[k] = v
    def get_extra(self, k, d=None): return self._extra.get(k, d)
    async def send(self, chain):
        comps = getattr(chain, "chain", None) or list(chain or [])
        self.sent.append([c for c in comps])


o = main.QqPeakGate.__new__(main.QqPeakGate)
o.cfg = _cfg
o.recent = {}
o.last_reply_ts, o.last_reply_to = {}, {}
o._log = lambda *a, **k: None
o._log_debug = lambda *a, **k: None
o._fault = lambda *a, **k: None
ev = Ev()
tgt = o._agent_target(ev)
asyncio.run(o._agent_send(tgt, "你好呀，我是大肥鱼～有问题先找 Synthia 呀", at_user_id="3245938285"))
_sents = getattr(ev, "sent", [])
_flat = [c for ch in _sents for c in ch]
ck("发出去了", len(_sents) == 1, str(_sents)[:80])
ck("带上了 At 组件（真的 @ 到人）", any(isinstance(c, At) for c in _flat),
   str([type(c).__name__ for c in _flat]))
ck("At 的是主人的 QQ 号",
   any(isinstance(c, At) and str(getattr(c, "qq", "")) == "3245938285" for c in _flat))
ck("正文没被吃掉", any(isinstance(c, Plain) and "大肥鱼" in str(c.text or "") for c in _flat))

asyncio.run(o._agent_send(tgt, "普通一句话"))
_flat2 = [c for ch in ev.sent[-1] for c in ch]
ck("不传 at_user_id 时不会莫名 @ 人", not any(isinstance(c, At) for c in _flat2))

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
