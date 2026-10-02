"""离线链路自测：在真实插件代码里跑"提示词分层 + 输出协议"。

复用 tests/gate_sim.py 的 AstrBot 桩，不联网、不调模型。
用法：python3 tests/flow_sim.py
"""
import asyncio
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gate_sim as G          # noqa: E402   （会装好桩并载入 main）
import replyproto             # noqa: E402
import promptlib              # noqa: E402

main = G.main
FAIL = []


def check(name, cond, extra=""):
    print("  %-44s %s%s" % (name, "OK" if cond else "FAIL", (" " + str(extra)) if extra else ""))
    if not cond:
        FAIL.append(name)


class Req:
    def __init__(self, prompt="在吗"):
        self.system_prompt = "旧提示词"
        self.prompt = prompt
        self.contexts = [{"role": "user", "content": "历史"}]
        self.func_tool = types.SimpleNamespace(tools=[1, 2, 3])
        self.image_urls = []
        self.extra_user_content_parts = []


def mk(plugin=None, gid="999999111"):
    ev = G.FakeEvent("在吗", uid="u1", gid=gid)
    return ev


async def main_():
    from astrbot.api.message_components import Plain as P

    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    p = G.new_plugin(cfg)
    if not hasattr(p, "recent"):
        p.recent = {}
    p._maybe_followup = lambda *a, **k: None          # 免得概率性去调模型

    print("== 提示词分层（真实 compact_context）==")
    ev = mk()
    req = Req()
    await p.compact_context(ev, req)
    sp = req.system_prompt or ""
    check("system_prompt 被替换成分层版本", "旧提示词" not in sp and len(sp) > 1500, "%d 字" % len(sp))
    check("含人格卡", "小鲸鱼" in sp or "群友" in sp)
    check("含行为层·安全规则", "【安全规则" in sp)
    check("含行为层·输出协议", "【输出协议" in sp and "|||" in sp and "[不说话]" in sp)
    check("含表情策略段", "【表情包" in sp)
    check("含可用表情包清单", "【可用表情包" in sp and "[表情:" in sp)
    check("历史上下文被清空（无状态）", req.contexts == [])
    check("工具 schema 被摘掉", not getattr(req.func_tool, "tools", None))

    print("== 输出协议·沉默 ==")
    res = types.SimpleNamespace(chain=[P("[不说话]")])
    ev2 = mk()
    ev2.get_result = lambda: res
    await p.smart_quote(ev2)
    check("[不说话] → 消息链清空（这条不发）", res.chain == [], res.chain)

    print("== 输出协议·分条 ==")
    res2 = types.SimpleNamespace(chain=[P("在的 ||| 叫我干嘛")])
    parts = []
    p._spawn_parts = lambda event, ps, delay=None, jitter=0.3: parts.append(list(ps))
    ev3 = mk()
    ev3.get_result = lambda: res2
    await p.smart_quote(ev3)
    check("第一条留下、其余按顺序补发", res2.chain[0].text == "在的" and parts == [["叫我干嘛"]],
          "%r %r" % (res2.chain[0].text, parts))

    print("== 输出协议·换行分条 ==")
    res5 = types.SimpleNamespace(chain=[P("好\n那我先睡了")])
    parts2 = []
    p._spawn_parts = lambda event, ps, delay=None, jitter=0.3: parts2.append(list(ps))
    ev5 = mk()
    ev5.get_result = lambda: res5
    await p.smart_quote(ev5)
    check("换行也按分条发", res5.chain[0].text == "好" and parts2 == [["那我先睡了"]],
          "%r %r" % (res5.chain[0].text, parts2))

    print("== 输出协议·指名表情 ==")
    st = main.stickers
    local = []
    for it in (st.load() or []):
        try:
            if st.abs_path(it) and os.path.isfile(st.abs_path(it)):
                local.append(it)
        except Exception:
            pass
    if local:
        sid = local[0]["id"]
        res3 = types.SimpleNamespace(chain=[P("笑死 [表情:%s]" % sid)])
        ev4 = mk(gid="999999222")
        ev4.get_result = lambda: res3
        st._ts_probe = True
        await p.smart_quote(ev4)
        kinds = [type(x).__name__ for x in res3.chain]
        await asyncio.sleep(0.4)          # 图片表情现在是后台单独发一条
        sent_kinds = [k for k in getattr(ev4, "sent_kinds", [])]
        check("[表情:id] 单独一条发出图片", any("Image" in k for k in sent_kinds),
              (kinds, sent_kinds))
        check("文字里的标记被清掉", all("[表情" not in getattr(x, "text", "") for x in res3.chain))
    else:
        check("库里有本地表情可测", False, "跳过")

    print()
    if FAIL:
        print("失败 %d 项：%s" % (len(FAIL), "、".join(FAIL)))
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    asyncio.run(main_())
