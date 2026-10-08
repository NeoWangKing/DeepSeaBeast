"""离线验证：收图关只收 QQ 表情（sub_type=1），普通图片不收；表情尽量卡通、有趣好玩。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/sticker_collect_sim.py
"""
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import stickers as S          # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


print("== 识图规则：只收表情 + 卡通/有趣 ==")
_src = open(os.path.join(ROOT, "stickers.py"), encoding="utf-8").read()
ck("规则里写明只收表情/不收图片", "只收表情" in _src and "不收图片" in _src)
ck("规则里要卡通风格", "卡通" in _src)
ck("规则里有有趣好玩的 fun 打分", "fun" in _src and "0~10" in _src)

print("== judge_collect：普通图片一律不收 ==")
ck("sub_type=0（照片）不收", S.judge_collect({}, sub_type=0)[0] is False)
ck("拿不到 sub_type 也不收（保守）", S.judge_collect({}, sub_type=None)[0] is False)
_g = {"kind": "cartoon", "style": "cartoon", "is_meme": True, "worth": True, "fun": 9}
ck("sub_type=1 的表率收", S.judge_collect(dict(_g), sub_type=1)[0] is True)
ck("群里的普通图片（识图说好玩）仍然不收",
   S.judge_collect(dict(_g), sub_type=0)[0] is False)
ck("主人说收藏 → 普通图片才放行",
   S.judge_collect(dict(_g), sub_type=0, explicit=True)[0] is True)
ck("…但实拍照片说了也不收",
   S.judge_collect({"kind": "photo", "style": "real", "is_meme": True, "worth": True,
                    "fun": 9}, sub_type=0, explicit=True)[0] is False)

print("== judge_collect：表情内部还要挑卡通/有趣的 ==")
for _k in ("screenshot", "poster", "photo", "group_photo", "qr", "ad"):
    ck("是表情但识图判成 %s → 不收" % _k,
       S.judge_collect({"kind": _k, "is_meme": True, "worth": True, "fun": 9},
                       sub_type=1)[0] is False)
ck("纯文字图不收", S.judge_collect({"kind": "meme", "style": "text", "is_meme": True,
                                    "worth": True, "fun": 8}, sub_type=1)[0] is False)
ck("实拍/真人风格不够好玩（fun=5）不收",
   S.judge_collect({"kind": "meme", "style": "real", "is_meme": True, "worth": True,
                    "fun": 5}, sub_type=1)[0] is False)
ck("实拍但特别好玩的（fun=9）能收",
   S.judge_collect({"kind": "meme", "style": "real", "is_meme": True, "worth": True,
                    "fun": 9}, sub_type=1)[0] is True)
ck("卡通风格 fun=6 能收",
   S.judge_collect({"kind": "cartoon", "style": "cartoon", "is_meme": True, "worth": True,
                    "fun": 6}, sub_type=1)[0] is True)
ck("不好玩（fun=2）卡通也不收",
   S.judge_collect({"kind": "cartoon", "style": "cartoon", "is_meme": True, "worth": True,
                    "fun": 2}, sub_type=1)[0] is False)
ck("识图说没意思（worth=false）不收",
   S.judge_collect({"kind": "cartoon", "style": "cartoon", "is_meme": True, "worth": False,
                    "fun": 9}, sub_type=1)[0] is False)
ck("识图判 2400px 太大 → 不收",
   S.judge_collect(dict(_g), sub_type=1, long_side=2400)[0] is False)
ck("识图失败但是真表情 → 先收下", S.judge_collect({}, sub_type=1)[0] is True)
ck("收的时候给出理由", bool(S.judge_collect(dict(_g), sub_type=1)[1]))

print("== image_size ==")
_real = [x for x in (S.load() or []) if x.get("file")]
if _real:
    _p = S.abs_path(_real[0])
    ck("能读到真图的长边", S.image_size(_p) > 0, str(S.image_size(_p)))
ck("坏路径不炸", S.image_size("/tmp/definitely-not-here.jpg") == 0)

print("== 接线 ==")
_msrc = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("收图关调用了 judge_collect", "stickers.judge_collect(" in _msrc)
ck("从原始报文取 sub_type", "_img_kinds" in _msrc and "sub_type" in _msrc)
ck("普通图片不再进收藏（日志有话说）", "不是 QQ 表情" in _msrc)
ck("存了 kind/style/fun 便于审计", '"kind": str((ge or {}).get("kind")' in _src)

print("== 真跑：走一遍 _spawn_sticker_collect ==")
try:
    import main
    from astrbot.api.message_components import Image
except Exception as e:
    print("  （跳过：%s）" % str(e)[:70])
    print()
    sys.exit(1 if FAIL else 0)

RAW_TP = {"message": [{"type": "image", "data": {
    "url": "https://example.invalid/x.jpg", "file": "a.jpg",
    "sub_type": 1, "summary": "[动画表情]"}}]}
RAW_PIC = {"message": [{"type": "image", "data": {
    "url": "https://example.invalid/y.jpg", "file": "b.jpg",
    "sub_type": 0, "summary": "[图片]"}}]}


class Ev:
    def __init__(self, raw=None, gid="869622030", text="", uid="287383512"):
        self.raw_message = raw or {}
        self.message_str = text
        self._gid, self._uid = gid, uid

    def get_group_id(self): return self._gid
    def get_sender_id(self): return self._uid
    def get_self_id(self): return "3237702352"
    def get_sender_name(self): return "群友"
    def get_messages(self): return [Image(file="/tmp/x.jpg")]
    def get_result(self):
        class R: chain = []
        return R()


def run_collect(ev, verdict, explicit_recent=None):
    o = main.QqPeakGate.__new__(main.QqPeakGate)
    o.cfg = {"stickers": {"enabled": True, "collect_max_per_hour": 10,
                          "send_exclude_groups": [], "collect_exclude_groups": []}}
    o.recent = {}
    if explicit_recent:
        o.recent[main.QqPeakGate._chat_key(ev)] = [("主人", explicit_recent, "3245938285")]
    logs, saved = [], []
    o._log = lambda *a, **k: logs.append(" ".join(str(x) for x in a))
    o._log_debug = o._log
    o._fault = lambda *a, **k: None
    o._save_tmp_image = lambda comp: "/tmp/self_face_sim_img.png"
    o._maybe_say_about_sticker = None

    async def _noop(*a, **k):
        return None
    o._maybe_say_about_sticker = _noop
    S.tag_image = lambda path, model="": dict(verdict)
    S.image_size = lambda path: 300
    S.add_file = lambda *a, **k: (saved.append((a[1], (a[6] if len(a) > 6 else {}) or
                                             {})), {"id": "sNEW", "desc": a[2], "tags": a[3]})[1]
    S.add_face = lambda path: False

    async def _main():
        o._spawn_sticker_collect(ev)
        for _ in range(6):
            await asyncio.sleep(0.05)
    asyncio.run(_main())
    return logs, saved


_ORIG = {}
for _f in ("tag_image", "image_size", "add_file", "add_face"):
    _ORIG[_f] = getattr(S, _f)

V_MEME = {"kind": "cartoon", "style": "cartoon", "is_meme": True, "worth": True,
          "fun": 9, "desc": "可爱表情", "tags": ["可爱"]}
logs, saved = run_collect(Ev(raw=RAW_TP), V_MEME)
ck("群里的 QQ 表情 → 收了", len(saved) == 1, str(saved)[:60])
ck("…日志写清是表情", any("表情包：收藏" in x for x in logs), str(logs)[:80])

logs, saved = run_collect(Ev(raw=RAW_PIC), V_MEME)
ck("群里的普通图片 → 不收（哪怕识图说好玩）", len(saved) == 0, str(logs)[:90])
ck("…日志说明原因", any("不是 QQ 表情" in x for x in logs), str(logs)[:90])

logs, saved = run_collect(Ev(raw=RAW_PIC, gid="", uid="3245938285"), V_MEME)
ck("主人私聊发图（没说要收藏）→ 也不收", len(saved) == 0, str(logs)[:90])

logs, saved = run_collect(Ev(raw=RAW_PIC, gid="", uid="3245938285"), V_MEME,
                          explicit_recent="帮我收藏这张")
ck("主人明确说收藏 → 普通图片也收", len(saved) == 1, str(logs)[:90])

logs, saved = run_collect(Ev(raw=RAW_PIC), {"kind": "photo", "style": "real",
                                            "is_meme": True, "worth": True, "fun": 9,
                                            "desc": "团队合影"})
ck("照片/合影不进来", len(saved) == 0, str(logs)[:90])

logs, saved = run_collect(Ev(raw=RAW_TP), {"kind": "screenshot", "style": "real",
                                           "is_meme": True, "worth": True, "fun": 9,
                                           "desc": "游戏截图"})
ck("是表情元素但识图判成截图 → 不收", len(saved) == 0, str(logs)[:100])

logs, saved = run_collect(Ev(raw=RAW_TP), {})
ck("识图整个失败但确实是表情 → 先收下", len(saved) == 1, str(logs)[:90])

logs, saved = run_collect(Ev(raw={}), V_MEME)
ck("拿不到原始报文（老平台）→ 不收，别乱收图片", len(saved) == 0, str(logs)[:90])

for _f, _fn in _ORIG.items():
    setattr(S, _f, _fn)

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
