"""补丁 11：三件套落地 —— 她能看到图（DeepSeek 多模态）、能主动拍一拍、能自己写记忆。

  agent/tools.py   新增 view_image / send_poke / memory_append
  main.py          callbacks + loop 带图直发多模态 + 记忆注入/落盘
"""
import sys


def patch(path, pairs, label):
    with open(path, encoding="utf-8") as f:
        s = f.read()
    for tag, old, new in pairs:
        n = s.count(old)
        if n != 1:
            print("!! %s/%s 匹配数=%d" % (label, tag, n))
            sys.exit(1)
        s = s.replace(old, new)
        print("  ok %s/%s" % (label, tag))
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)


# ── tools.py：三个方法 ─────────────────────────────────────────────────────
M_OLD = '''    # ---------- 收尾类 ----------'''
M_NEW = '''    # ---------- 看图 / 拍一拍 / 写记忆 ----------
    def view_image(self) -> str:
        """看当前这批消息里的图（她真的"看"：交给 DeepSeek 多模态）。"""
        fn = self.cb.get("view_image")
        if not fn:
            return "看不了：这个会话没开看图"
        try:
            txt = fn()
        except Exception as e:
            return "看不了：%r" % (e,)
        return str(txt) if txt else "这张图我看不出来是什么"

    def send_poke(self, target_id="") -> str:
        fn = self.cb.get("send_poke")
        if not fn:
            return "拍不了：这个会话没开拍一拍"
        try:
            ok = fn(str(target_id or ""))
        except Exception as e:
            return "拍不了：%r" % (e,)
        return "拍了一下" if ok else "拍不了（对方不在这个群？）"

    def memory_append(self, text="", kind="impression") -> str:
        """自己往记忆里写一条：印象 / 没聊完的话题 / 想说没说的话。"""
        t = str(text or "").strip()
        if not t:
            return "没记：text 是空的（把要记的一句话放进 text）"
        fn = self.cb.get("memory_append")
        if not fn:
            return "记不了：这个会话没开记忆写入"
        try:
            return str(fn(str(kind or "impression"), t[:120]) or "记下了")
        except Exception as e:
            return "记不了：%r" % (e,)

    # ---------- 收尾类 ----------'''
patch("agent/tools.py", [("methods", M_OLD, M_NEW)], "agent/tools.py")

SPEC_OLD = '''    if on("finish"):'''
SPEC_NEW = '''    if on("vision") and send_tools:
        specs.append(("view_image", [],
                      "看当前消息里的图片（她真的能看到图再说话）；消息里带 [图片] 时优先用它", "view_image"))
    if on("poke") and send_tools:
        specs.append(("send_poke",
                      [{"type": "string", "name": "target_id",
                        "description": "可选：戳谁的 QQ 号，留空戳当前说话的人"}],
                      "拍一拍对方（偶尔逗熟人用，别频繁）", "send_poke"))
    if on("memory_write"):
        specs.append(("memory_append",
                      [{"type": "string", "name": "text", "description": "【必填】要记的一句话（≤120字）"},
                       {"type": "string", "name": "kind",
                        "description": "impression=对某人的印象 / topic=没聊完的话题 / thought=想说的话"}],
                      "把值得记住的东西写进自己的记忆（偶尔用，别当流水账）", "memory_append"))
    if on("finish"):'''
patch("agent/tools.py", [("specs", SPEC_OLD, SPEC_NEW)], "agent/tools.py")

# ── loop.py：required 里加 text ─────────────────────────────────────────────
patch("agent/loop.py", [("req", '''REQUIRED = {"send_message": ["text"], "send_sticker": ["sticker_id"],
            "collect_sticker": ["message_id"]}''',
                         '''REQUIRED = {"send_message": ["text"], "send_sticker": ["sticker_id"],
            "collect_sticker": ["message_id"], "memory_append": ["text"]}''')], "agent/loop.py")

print("tools/loop 完成")


# ── 补丁 11b：接线（callbacks / 看图直发 / 记忆 / 私聊闸门）────────────────────
patch("agent/__init__.py", [("voir", "from . import llm, loop, tools  # noqa: F401",
                             "from . import llm, loop, tools, vision  # noqa: F401")], "agent/__init__.py")
patch("agent/__init__.py", [("voir2", '__all__ = ["tools", "loop", "llm"]',
                             '__all__ = ["tools", "loop", "llm", "vision"]')], "agent/__init__.py")

# 1) callbacks：三个新工具 + 记忆落盘
CB_OLD = '''        cbs = {"recent_lines": _recent, "active_members": _members,
               "memory_lookup": _memory, "list_stickers": _list_stickers}'''
CB_NEW = '''        def _view_image():
            comps = [m for m in event.get_messages() if isinstance(m, Image)]
            if not comps:
                return ""
            paths = []
            for c in comps[:2]:
                try:
                    pth = self._save_tmp_image(c)
                    if pth:
                        paths.append(pth)
                except Exception:
                    pass
            if not paths:
                return ""
            txt = agent.vision.describe(paths, self.cfg)
            return ("图里大概是这样：%s" % txt) if txt else ""

        def _send_poke(target=""):
            uid = str(target or event.get_sender_id() or "")
            gid = str(event.get_group_id() or "")

            async def _do():
                params = {"user_id": int(uid)}
                if gid and gid != "0":
                    params["group_id"] = int(gid)
                await event.call_action("send_poke", **params)
            try:
                _spawn(_do())
                return True
            except Exception:
                return False

        def _memory_append(kind, text):
            try:
                d = os.path.join(PLUGIN_DIR, "data", "agent_memory")
                os.makedirs(d, exist_ok=True)
                fn = os.path.join(d, (self._chat_key(event).replace(":", "_") or "x") + ".jsonl")
                with open(fn, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"ts": int(time.time()), "kind": str(kind or "impression")[:16],
                                         "text": str(text or "")[:120]}, ensure_ascii=False) + "\\n")
                return "记下了"
            except Exception as e:
                return "没记住：%r" % (e,)

        cbs = {"recent_lines": _recent, "active_members": _members,
               "memory_lookup": _memory, "list_stickers": _list_stickers,
               "view_image": _view_image, "send_poke": _send_poke,
               "memory_append": _memory_append}'''
patch("main.py", [("cb-new", CB_OLD, CB_NEW)], "main.py")

# 2) loop：带图直发多模态 + 注入她自己写的记忆 + caps.vision=True
LOOP_OLD = '''        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user", "content": user}]'''
LOOP_NEW = '''        # 她自己写的记忆：最近几条注入到提示词末尾
        try:
            _mf = os.path.join(PLUGIN_DIR, "data", "agent_memory",
                               (key.replace(":", "_") or "x") + ".jsonl")
            if os.path.isfile(_mf):
                _notes = []
                with open(_mf, encoding="utf-8") as _fh:
                    for _line in _fh.readlines()[-8:]:
                        try:
                            _o = json.loads(_line)
                            _notes.append("- [%s] %s" % (_o.get("kind"), _o.get("text")))
                        except Exception:
                            pass
                if _notes:
                    system_prompt += "\\n\\n【你自己之前记下的（仅供参考，别硬提）】\\n" + "\\n".join(_notes)
        except Exception:
            pass
        # 消息里有图 → 直接把图给多模态模型看（她真的能看到），这一轮临时切模型
        _img_paths = []
        try:
            for _m in event.get_messages():
                if isinstance(_m, Image):
                    _p = self._save_tmp_image(_m)
                    if _p:
                        _img_paths.append(_p)
        except Exception:
            pass
        _vmodel = None
        _user_content = user
        if _img_paths:
            _parts = []
            for _p in _img_paths[:2]:
                try:
                    _b64 = base64.b64encode(open(_p, "rb").read()).decode()
                    _ext = os.path.splitext(_p)[1].lstrip(".").lower() or "jpeg"
                    _parts.append({"type": "image_url",
                                   "image_url": {"url": "data:image/%s;base64,%s" % (_ext, _b64)}})
                except Exception:
                    pass
            if _parts:
                _parts.append({"type": "text", "text": user})
                _user_content = _parts
                _vmodel = agent.vision.vision_model(self.cfg)
                self._log("agent loop：带 %d 张图 → 用 %s 直接看图" % (len(_parts) - 1, _vmodel))
        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user", "content": _user_content}]'''
patch("main.py", [("loop-vision", LOOP_OLD, LOOP_NEW)], "main.py")

patch("main.py", [("loop-run", '''        r = await asyncio.to_thread(agent.loop.run, t, messages, schema, None, _rounds, self._log_debug)''',
                   '''        r = await asyncio.to_thread(agent.loop.run, t, messages, schema, None, _rounds,
                                    self._log_debug, _vmodel)''')], "main.py")

patch("main.py", [("caps-vision", '''            caps={"vision": False, "search": False,
                  "kb": bool(_kcfg.get("enabled", True) and not private),''',
                   '''            caps={"vision": True, "search": False,
                  "kb": bool(_kcfg.get("enabled", True) and not private),''')], "main.py")

# 3) 私聊闸门：私聊里也走 loop（只在开启的会话生效）
PRIV = '''    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def gate_private(self, event: AstrMessageEvent) -> None:
        """私聊：只在开了 agent loop 的会话里接管（其他私聊完全不碰）。"""
        try:
            self._reload_cfg()
            if self._agent_loop_on(event):
                await self._allow(event)
        except Exception as e:
            self._log("私聊闸门出错（忽略）: %r" % (e,))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)'''
patch("main.py", [("priv-gate", '''    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)''', PRIV)],
      "main.py")

# 4) 配置：三件套打开；闪电群 + 私聊(owner) 开 loop
import json as _json
_c = _json.load(open("config.json", encoding="utf-8"))
_a = _c.setdefault("agent", {})
_a.setdefault("tools", {}).update({"vision": True, "poke": True, "memory_write": True})
_a["loop_mode"] = True
_a["send_tools"] = True
_a["loop_groups"] = ["869622030", "p:3245938285"]
_a["send_tools_groups"] = ["869622030", "p:3245938285"]
_a.setdefault("llm", {}).setdefault("vision_model", "deepseek-flash")
_a["max_rounds"] = 2
_json.dump(_c, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("补丁 11b 完成；loop_groups=%s tools=%s" % (_a["loop_groups"], _a["tools"]))
