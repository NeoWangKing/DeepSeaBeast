#!/usr/bin/env python3
""""话说完没有"判断自测。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.typing import looks_unfinished  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-42s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


def main() -> int:
    print("== 没说完 ==")
    for t in ("我跟你", "我", "然后", "因为他", "那个", "我想说，", "我觉得……", "就是说"):
        ck("「%s」→ 等" % t, looks_unfinished(t) is True)

    print()
    print("== 说完了 ==")
    for t in ("在吗", "你好", "然后呢", "今天天气不错。", "你吃饭了吗？",
              "帮我看看这个报错", "分两条发：先夸我一句，再损我一句，都短点",
              "哈哈哈", "好的", ""):
        ck("「%s」→ 回" % (t or "（空）"), looks_unfinished(t) is False)

    print()
    print("== 边界 ==")
    ck("省略号结尾算没说完", looks_unfinished("我想想…") is True)
    ck("问号结尾算说完", looks_unfinished("你去不去？") is False)
    ck("长句没标点也算说完", looks_unfinished("帮我把这个函数改成异步的实现") is False)
    ck("短句带感叹号算说完", looks_unfinished("牛啊！") is False)

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
