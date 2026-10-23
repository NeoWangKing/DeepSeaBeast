#!/usr/bin/env python3
"""待办自测：跨回合留存、上限拒接、到点提醒、分区隔离。"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.todos import TodoStore, OPEN  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


def main() -> int:
    root = tempfile.mkdtemp()
    s = TodoStore(root, max_open=2, stale_days=7, remind_per_day=2)

    print("== 基本读写 ==")
    a, note = s.add("g1", "整理表格")
    ck("能记下", a is not None and a.text == "整理表格")
    ck("提示剩余名额", "剩" in note, note)
    ck("能列出来", [t.text for t in s.open_items("g1")] == ["整理表格"])

    print()
    print("== 跨重启还在（换一个 store 实例读同一个目录）==")
    s2 = TodoStore(root, max_open=2)
    ck("新实例读得到", [t.text for t in s2.open_items("g1")] == ["整理表格"])

    print()
    print("== 会话之间隔离 ==")
    s.add("p1", "私聊的事")
    ck("群里的看不到私聊的", [t.text for t in s.open_items("g1")] == ["整理表格"])
    ck("私聊的独立", [t.text for t in s.open_items("p1")] == ["私聊的事"])

    print()
    print("== 上限：满了要拒绝，并说明压着几件 ==")
    b, _ = s.add("g1", "第二件")
    ck("第二件能记", b is not None)
    bad, why = s.add("g1", "第三件")
    ck("第三件被拒", bad is None, str(why)[:40])
    ck("拒绝时说清上限", "上限" in why, why[:40])

    print()
    print("== 收掉以后能接新的；全收完会提示可以接活 ==")
    ok, msg = s.done("g1", a.id)
    ck("收掉一件", ok and "还剩 1 件" in msg, msg)
    c, _ = s.add("g1", "第三件")
    ck("腾出名额后能记", c is not None)
    ok2, msg2 = s.done("g1", b.id)
    ok3, msg3 = s.done("g1", c.id)
    ck("全收完提示可以接新的", ok3 and "都做完了" in msg3, msg3)

    print()
    print("== 到点提醒：只提醒一次，且有每日上限 ==")
    r = TodoStore(tempfile.mkdtemp(), max_open=5, remind_per_day=2)
    d, _ = r.add("g1", "十分钟后喝水", due=time.time() - 1)
    e, _ = r.add("g1", "另一个到点的", due=time.time() - 1)
    due = r.due_now()
    ck("两个都到点了", len(due) == 2, str([t.text for _sc, t in due]))
    ck("标记提醒成功", r.mark_reminded("g1", d.id))
    ck("同一条不会重复提醒", len(r.due_now()) == 1)
    ck("第二个也提醒（今天第 2 次）", r.mark_reminded("g1", e.id))
    f, _ = r.add("g1", "第三条到点", due=time.time() - 1)
    ck("超过每日上限就不提醒了", r.mark_reminded("g1", f.id) is False)
    ck("没到点的不提醒", r.add("g2", "一小时后的", due=time.time() + 3600)[0] is not None
       and all(t.text != "一小时后的" for _sc, t in r.due_now()))

    print()
    print("== 作废 ==")
    g, _ = s.add("p1", "不做了的事")
    ok, msg = s.drop("p1", g.id)
    ck("能作废", ok and "不做了" in msg, msg)
    ck("作废后不在未完成列表", [t.text for t in s.open_items("p1")] == ["私聊的事"])

    print()
    print("== 工具层（核心内置，任何通道都有）==")
    from core.tools import DEFAULT_SPECS, schema
    names = [x["function"]["name"] for x in schema(DEFAULT_SPECS, {"text"})]
    ck("终端也有待办工具", {"add_todo", "list_todos", "done_todo", "drop_todo"} <= set(names), str(names))
    from core.capability import glossary
    ck("待办不算对外能力（不进能力清单）", all("待办" not in x for x in glossary(["说话"])))

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
