#!/usr/bin/env python3
"""分层记忆 + 上下文装配自测：隔离、顺序、预算裁剪、重启读回。"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.channel import ChatRef, InboundMessage, Message, Sender  # noqa: E402
from core.context import ContextBundle, ContextRegistry, estimate_tokens  # noqa: E402
from core.memory import MemoryStore  # noqa: E402
from core.prompt import render  # noqa: E402
from core.scope import Scope  # noqa: E402
from core.store import RecentStore  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-48s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


class Provider:
    """假的记忆插件：往 scope 里加一条知识。"""

    def __init__(self, name: str, priority: int, text: str, persona: str = "",
                 can_persona: bool = False) -> None:
        self.name = name
        self.priority = priority
        self.text = text
        self.persona = persona
        self.can_persona = can_persona

    async def contribute(self, bundle):
        from core.context import Contribution
        return Contribution(priority=self.priority, knowledge=[self.text],
                            persona=self.persona)


def mk(msg_text: str = "在吗", gid: int = 1109525246) -> InboundMessage:
    return InboundMessage(chat=ChatRef.group(gid), sender=Sender(1, "灵其啊"),
                          message=Message.from_text(msg_text), message_id="1", at_me=True)


async def main() -> int:
    td = Path(tempfile.mkdtemp())
    try:
        mem = MemoryStore(td / "memory")
        recent = RecentStore(td / "recent")

        print("== self 层 ==")
        mem.write_self("identity", "你是大肥鱼，一条嘴有点毒的鱼。")
        mem.write_self("owner", "主人在写一个叫 breamer 的 Rust 项目。")
        parts = mem.self_parts()
        ck("identity 读得到", "大肥鱼" in parts.get("identity", ""))
        ck("owner 读得到", "breamer" in parts.get("owner", ""))

        print()
        print("== scope 层隔离 ==")
        g1, g2 = Scope.from_chat(ChatRef.group(1109525246)), Scope.from_chat(ChatRef.group(869622030))
        mem.learn_scope(g1, "profile", "这个群在聊终末地。")
        mem.learn_scope(g1, "jargon", {"terms": [{"term": "yjwl", "meaning": "鹰角网络"}]})
        mem.learn_scope(g2, "profile", "这个群在聊 CS。")
        ck("群1 有 yjwl", "yjwl" in str(mem.scope_parts(g1)))
        ck("群1 看不到群2 的印象", "CS" not in str(mem.scope_parts(g1)))
        ck("群2 看不到群1 的印象", "终末地" not in str(mem.scope_parts(g2)))

        print()
        print("== 渲染顺序（缓存友好）==")
        reg = ContextRegistry()
        reg.add(Provider("mem", 20, "【记忆】这个群在聊终末地"))
        reg.add(Provider("persona", 5, "", persona="你是大肥鱼。", can_persona=True))
        b = ContextBundle(persona="", budget_tokens=6000, current=mk())
        b.rules = ["一次最多说 4 条"]
        b.self_knowledge = ["主人在写 breamer"]
        b.scene = "当前场景：QQ群"
        await reg.collect(b)
        msgs = render(b)
        ck("第 1 段是人格+规则", "大肥鱼" in msgs[0]["content"] and "一次最多说 4 条" in msgs[0]["content"])
        ck("第 2 段是 self 记忆", "breamer" in msgs[1]["content"])
        ck("第 3 段是 scope 记忆", "终末地" in msgs[2]["content"])
        ck("第 4 段是场景", "当前场景" in msgs[3]["content"])
        ck("最后是当前这句", msgs[-1]["role"] == "user" and "在吗" in msgs[-1]["content"])

        print()
        print("== 插件不能覆盖人格（除非 can_persona）==")
        reg2 = ContextRegistry()
        reg2.add(Provider("bad", 10, "x", persona="你是客服", can_persona=False))
        b2 = ContextBundle(persona="你是大肥鱼。", current=mk())
        await reg2.collect(b2)
        ck("普通人不能改人格", b2.persona == "你是大肥鱼。", b2.persona)

        print()
        print("== 预算裁剪：低优先级先丢 ==")
        reg3 = ContextRegistry()
        reg3.add(Provider("high", 10, "高优先级" * 40))
        reg3.add(Provider("low", 90, "低优先级" * 40))
        b3 = ContextBundle(budget_tokens=120, current=mk())
        await reg3.collect(b3)
        before = b3.tokens()
        b3.trim()
        left = " ".join(t for _, t in b3.scope_knowledge)
        ck("超预算会裁剪", b3.tokens() <= 120, "%d → %d" % (before, b3.tokens()))
        ck("先丢低优先级", "低优先级" not in left and "高优先级" in left, left[:20])

        print()
        print("== recent 落盘 + 重启读回 ==")
        recent.append("g1109525246", "灵其啊", "你好", 1, 1.0)
        recent.append("g1109525246", "我", "在的", 0, 2.0)
        again = RecentStore(td / "recent")          # 模拟重启
        rows = again.get("g1109525246")
        ck("重启后能读回", len(rows) == 2 and rows[-1][1] == "在的", str(rows))
        ck("另一个会话不受影响", again.get("g869622030") == [])
    finally:
        shutil.rmtree(td, ignore_errors=True)

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
