#!/usr/bin/env python3
"""人格插件自测：按作用域选卡、注入人格、不能越权改别人的。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.channel import ChatRef, InboundMessage, Message, Sender  # noqa: E402
from core.context import ContextBundle, ContextRegistry  # noqa: E402
from core.plugin import PluginManager  # noqa: E402
from core.prompt import render  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-50s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


def mk(kind: str = "group", cid: int = 100000001, uid: int = 200000001) -> InboundMessage:
    chat = {"group": ChatRef.group, "private": ChatRef.private}.get(kind, lambda x: ChatRef.local())(cid)
    return InboundMessage(chat=chat, sender=Sender(uid, "灵其啊"),
                          message=Message.from_text("在吗"), message_id="1", addressed=True)


async def main() -> int:
    import json, tempfile, os
    py = str(ROOT / ".venv/bin/python")
    data_dir = tempfile.mkdtemp()
    # 测试自己捏一张"群卡"和映射——不依赖仓库里任何真实群的卡
    card = os.path.join(data_dir, "cards", "fixture-group")
    os.makedirs(card, exist_ok=True)
    with open(os.path.join(card, "prompt.txt"), "w", encoding="utf-8") as f:
        f.write("你是「测试鱼」，这个群里的一员。不要像 AI 助手那样说话。")
    with open(os.path.join(card, "memes.md"), "w", encoding="utf-8") as f:
        f.write("蒂蒂、狗头这些梗可以用。")
    with open(os.path.join(card, "meme-mode.md"), "w", encoding="utf-8") as f:
        f.write("梗模式：轻松接梗，别硬玩。")
    with open(os.path.join(data_dir, "mapping__custom__.json"), "w", encoding="utf-8") as f:
        json.dump({"by_group": {"100000001": "fixture-group",
                                "100000004": "general/friend"},
                   "by_private": {"200000001": "general/private"},
                   "by_channel": {"cli": "general/terminal", "tcp": "general/terminal",
                                  "socket": "general/terminal-script"}}, f, ensure_ascii=False)

    mgr = PluginManager()
    await mgr.load({"name": "personas", "command": [py, "plugins/personas/main.py"],
                    "cwd": str(ROOT), "timeout": 10, "priority": 5,
                    "can_persona": True, "data_dir": data_dir}, base_dir=str(ROOT))

    providers = mgr.context_providers()
    ck("插件声明了 context 能力", len(providers) == 1, str([p.name for p in providers]))

    reg = ContextRegistry()
    for p in providers:
        reg.add(p)

    import json as _json, os as _os

    async def reload_plugin():
        """插件跑在子进程里，改设置后要重载才能生效。"""
        nonlocal mgr, reg
        await mgr.close()
        mgr = PluginManager()
        await mgr.load({"name": "personas", "command": [py, "plugins/personas/main.py"],
                        "cwd": str(ROOT), "timeout": 10, "priority": 5,
                        "can_persona": True, "data_dir": data_dir}, base_dir=str(ROOT))
        reg = ContextRegistry()
        for p in mgr.context_providers():
            reg.add(p)

    def set_mapping(d: dict):
        with open(_os.path.join(data_dir, "mapping__custom__.json"), "w", encoding="utf-8") as f:
            _json.dump(d, f, ensure_ascii=False)

    async def persona_for(msg, channel="onebot"):
        b = ContextBundle(scope_key=msg.chat.key, scope_kind=msg.chat.kind,
                          scope_id=msg.chat.id, sender_id=msg.sender.id,
                          sender_name=msg.sender.name, current=msg,
                          channel_name=channel)
        await reg.collect(b)
        return (b.persona or ""), render(b)

    print()
    print("== 卡片目录规范（export 优先 / 白名单追加 / 梗模式开关）==")
    std = _os.path.join(data_dir, "cards", "std")
    _os.makedirs(_os.path.join(std, "export"), exist_ok=True)
    with open(_os.path.join(std, "export", "system-prompt.txt"), "w", encoding="utf-8") as f:
        f.write("正文：我是标准卡。")
    with open(_os.path.join(std, "profile.md"), "w", encoding="utf-8") as f:
        f.write("考据资料，不该进提示词。")
    with open(_os.path.join(std, "voice.md"), "w", encoding="utf-8") as f:
        f.write("说话风格：短句。")
    with open(_os.path.join(std, "meme-mode.md"), "w", encoding="utf-8") as f:
        f.write("梗模式：接梗。")
    set_mapping({"by_group": {"100000001": "fixture-group", "100000009": "std",
                              "100000004": "general/friend"},
                 "by_private": {"200000001": "general/private"},
                 "by_channel": {"cli": "general/terminal", "tcp": "general/terminal",
                                "socket": "general/terminal-script"}})
    await reload_plugin()
    p_std, _ = await persona_for(mk("group", 100000009))
    ck("优先用 export/system-prompt.txt", "我是标准卡" in p_std, p_std[:30])
    ck("profile.md 不追加", "考据资料" not in p_std, p_std[:60])
    ck("voice.md 会追加", "说话风格" in p_std, p_std[-30:])
    ck("梗模式默认不开", "梗模式" not in p_std, p_std[-30:])

    st = _json.loads(open(_os.path.join(data_dir, "settings__custom__.json"), encoding="utf-8").read())
    st["meme_mode"] = True
    with open(_os.path.join(data_dir, "settings__custom__.json"), "w", encoding="utf-8") as f:
        _json.dump(st, f, ensure_ascii=False)
    await reload_plugin()
    p_std2, _ = await persona_for(mk("group", 100000009))
    ck("开梗模式后追加 meme-mode.md", "梗模式" in p_std2, p_std2[-30:])

    print()
    print("== 群人格 ==")
    p_dbg, msgs_dbg = await persona_for(mk("group", 100000001))
    ck("群卡按 mapping 选对了", "测试鱼" in p_dbg, p_dbg[:40])
    ck("目录卡带上了梗库与梗模式", "蒂蒂" in p_dbg and "梗模式" in p_dbg, p_dbg[-40:])
    ck("人格进了 messages[0]", p_dbg.splitlines()[0][:10] in msgs_dbg[0]["content"])

    p_light, _ = await persona_for(mk("group", 100000004))
    ck("闪电群人格不同", p_light != p_dbg, p_light[:30])
    ck("别的群仍然拼通用补丁", "AI 味" in p_light or "矜持" in p_light)

    print()
    print("== 按通道选卡 ==")
    from core.channel import ChatRef as CR
    from core.context import ContextBundle as CB

    async def persona_with_channel(channel: str):
        b = CB(scope_key="cli", scope_kind="local", scope_id=0,
               sender_id=1, sender_name="灵其啊", channel_name=channel)
        await reg.collect(b)
        return b.persona or ""

    p_cli = await persona_with_channel("cli")
    p_tcp = await persona_with_channel("tcp")
    p_sock = await persona_with_channel("socket")
    ck("cli 拿到终端卡", "终端" in p_cli, p_cli[:26])
    ck("tcp 也走终端卡", "终端" in p_tcp, p_tcp[:26])
    ck("socket 拿到脚本卡", "脚本接口" in p_sock, p_sock[:26])
    ck("三种通道的卡不同", len({p_cli[:20], p_sock[:20]}) == 2)

    print()
    print("== 私聊人格 ==")
    p_priv, _ = await persona_for(mk("private", 200000001, 200000001))
    ck("私聊拿到温和版", p_priv != p_dbg, p_priv[:30])
    p_other, _ = await persona_for(mk("private", 200000002, 200000002))
    ck("另一个私聊也不同", p_other != p_priv, p_other[:30])

    await mgr.close()
    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
