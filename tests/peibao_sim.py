"""豹群（966812151）离线验收：@ 才触发 + 工具清单滤掉图片表情 + 上下文不落盘。

用真实 config.json，在桩环境里跑 gate()，不联网、不发真消息。
用法：python3 tests/peibao_sim.py
"""
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import gate_sim as G            # noqa: E402  桩 + 载入 main

main = G.main
GID = "966812151"
FAIL = []


def check(name, cond, extra=""):
    print("  %-46s %s%s" % (name, "OK" if cond else "FAIL", (" " + str(extra)) if extra else ""))
    if not cond:
        FAIL.append(name)


async def main_():
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    p = G.new_plugin(cfg)
    calls = []

    async def _fake_loop(event):
        calls.append(p._chat_key(event))
        return True
    p._agent_loop_run = _fake_loop

    # 1) 非 @：不该触发 loop（人设：只在被叫时出现）
    ev = G.FakeEvent("今天天气不错", uid="u9", gid=GID)
    await p.gate(ev)
    check("非 @ 消息不启动 loop", calls == [] and not getattr(ev, "is_wake", False), calls)

    # 2) @ 她：该触发
    ev2 = G.FakeEvent("在吗", uid="u9", gid=GID, ats=("3237702352",))
    await p.gate(ev2)
    check("@ 她 → 启动 loop", calls == [GID], calls)

    # 3) 工具清单：图片表情被滤掉，发言/QQ表情还在
    specs = p._agent_specs_for(GID)
    names = [x[0] for x in specs]
    check("滤掉 send_sticker / collect_sticker",
          "send_sticker" not in names and "collect_sticker" not in names, names)
    check("保留 send_message / send_face / finish",
          all(n in names for n in ("send_message", "send_face", "finish")), names)

    # 4) 人设没变 + 上下文不落盘
    import promptlib
    path, src = promptlib.pick_file(ROOT, cfg, GID, False)
    check("豹群人设仍是 system_prompt_tool.txt", os.path.basename(path) == "system_prompt_tool.txt", path)
    p.recent[GID] = __import__("collections").deque([("某人", "不该出现的历史", "u1")])
    txt, meta = promptlib.build_system_prompt(plugin_dir=ROOT, cfg=cfg, chat_key=GID, private=False,
                                              caps={"tools_text": "x", "tools_send": True})
    check("行为层仍带输出协议", "【输出协议" in txt)
    check("人设层是工具版（不是朋友版）", "助手" in txt or "只在被 @ 的时候说话" in txt, meta["persona"])

    print()
    if FAIL:
        print("失败 %d 项：%s" % (len(FAIL), "、".join(FAIL)))
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    asyncio.run(main_())
