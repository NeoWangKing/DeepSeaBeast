#!/usr/bin/env python3
"""能力清单自测：只列"对外动作"，未验证的工具不给模型，控制流不出现。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.tools import DEFAULT_SPECS, schema, skill_line  # noqa: E402

FAIL: list[str] = []
QQ = {"text", "at", "quote", "image", "file", "face", "poke", "voice"}
TERM = {"text"}


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-48s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


def main() -> int:
    print("== 控制流不算能力 ==")
    line_qq = skill_line(DEFAULT_SPECS, QQ)
    line_term = skill_line(DEFAULT_SPECS, TERM)
    ck("清单里没有 decide", "decide" not in line_qq and "判断接不接" not in line_qq, line_qq[:60])
    ck("清单里没有 finish", "finish" not in line_qq and "收工" not in line_qq, line_qq[:60])

    print()
    print("== 未验证的工具不出现 ==")
    names_qq = [x["function"]["name"] for x in schema(DEFAULT_SPECS, QQ)]
    ck("send_file 未给模型", "send_file" not in names_qq, str(names_qq))
    ck("send_image 未给模型", "send_image" not in names_qq, str(names_qq))
    ck("清单里也没有发文件/发图片", "发文件" not in line_qq and "发图片" not in line_qq, line_qq)
    ck("已验证的仍在", {"send_message", "send_face"} <= set(names_qq), str(names_qq))

    print()
    print("== 清单随通道变化 ==")
    ck("QQ 有发表情", "发表情" in line_qq, line_qq[:40])
    ck("终端没有发表情", "发表情" not in line_term, line_term[:40])
    ck("两边都有说话", "说话" in line_qq and "说话" in line_term)

    print()
    print("== 查询类单独列 ==")
    ck("查表情名在清单里", "查表情名" in line_qq, line_qq)

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
