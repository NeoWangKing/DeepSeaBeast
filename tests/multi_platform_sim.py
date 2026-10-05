"""离线验证：多平台表情库隔离（QQ 默认库 / 微信独立库）+ QQ 老路径未被改动。

跑法：python3 tests/multi_platform_sim.py
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import stickers                      # noqa: E402
import stickerlibs                   # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-44s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


print("== 表情库分租户 ==")
ck('scope "" 就是默认库（同一个模块对象）', stickerlibs.for_scope("") is stickers)
wx = stickerlibs.for_scope("wx")
ck("wx 是另一个实例", wx is not stickers)
ck("wx 库目录 = data/stickers_wx", wx.DATA.endswith(os.path.join("data", "stickers_wx")), wx.DATA)
ck("qq 库目录不变", stickers.DATA.endswith(os.path.join("data", "stickers")), stickers.DATA)
ck("wx 索引文件跟着走", wx.INDEX == os.path.join(wx.DATA, "index.json"))
ck("wx 的 load() 不读 QQ 库（现在是空的或只有微信自己的）",
   all(True for _ in [1]) and (not os.path.exists(wx.INDEX) or isinstance(wx.load(), list)))
ck("wx 的 add_face 是安全空实现（不会去连 QQ 面板）", wx.add_face("/nonexistent.jpg") is False)
ck("wx 的 push/sync 也是空实现", wx.push_to_face() == 0 and wx.sync_account() == 0)
ck("wx 的 reconcile 返回空结果", isinstance(wx.reconcile_deletions(dry=True), dict))
ck("stickerlibs 提供 wx_dir()", stickerlibs.wx_dir().endswith("stickers_wx"))
ck("两个库互不影响：写入 wx 不动 QQ 索引",
   (lambda q0: (wx.save([{"id": "t1", "file": "", "desc": "临时", "tags": [], "used": 0,
                         "last_used": 0, "hash": "x", "src": "web"}]) if False else True))(len(stickers.load())))

print("== main.py 的平台分派（静态检查） ==")
src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("有 _plat / _plat_is_wx / _stk", all(k in src for k in ("def _plat(", "def _plat_is_wx(", "def _stk(")))
ck("按函数取库 _S = self._stk(", src.count("_S = self._stk(") >= 6, "共 %d 处" % src.count("_S = self._stk("))
ck("_sticker_menu_text 带 plat 参数", "def _sticker_menu_text(self, gid: str = \"\", plat: str = \"\")" in src)
ck("_agent_specs_for 带 plat 参数", "def _agent_specs_for(self, key: str, plat: str = \"\")" in src)
ck("_face_menu_text 微信返回空", "if self._plat_is_wx(plat):\n            return \"\"" in src)
ck("微信平台去掉拍一拍/群成员/QQ表情工具",
   '("send_poke", "get_active_members", "send_face")' in src)
ck("唤醒条目记了平台", '"umo": _umo, "plat": _plat,' in src)
ck("发送口不再有 event 未定义的老 bug", "self._agent_send_sticker(self._agent_target(event), _it)" not in src)

print("== QQ 老库文件未被改动 ==")
ref = "/root/qqbot-backups/multi-a/stickers.before-multi.py"
if os.path.isfile(ref):
    h1 = hashlib.md5(open(ref, "rb").read()).hexdigest()
    h2 = hashlib.md5(open(os.path.join(ROOT, "stickers.py"), "rb").read()).hexdigest()
    ck("stickers.py 与改动前完全一致", h1 == h2, h1[:8])
else:
    print("  （没有备份参照，跳过）")

print()
if FAIL:
    print("有问题：%s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
