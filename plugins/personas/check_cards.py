#!/usr/bin/env python3
"""人格卡体检：扫一遍所有卡，报告"用哪个文件当正文、多少字、缺什么"。

用法：
  .venv/bin/python plugins/personas/check_cards.py
  .venv/bin/python plugins/personas/check_cards.py --dir ~/Projects/persona_cards
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main as P  # noqa: E402


def check_dir(path: str, prefix: str = "") -> int:
    st = P.load_settings()
    limit = int(st.get("max_chars") or 12000)
    bad = 0
    rows = []
    for name in sorted(os.listdir(path)):
        if name.startswith("."):          # .git / .gitignore 之类不是卡
            continue
        full = os.path.join(path, name)
        if os.path.isdir(full):
            text = P._read_dir_card(full)
            if not text:
                # 没正文的目录可能是"分类容器"（比如 channels/ 下面一张张通道卡）→ 往下看一眼
                sub = [n for n in os.listdir(full) if not n.startswith(".")]
                if sub:
                    rows.append((prefix + name + "/", "📁 容器目录，往下检查", 0))
                    bad += check_dir(full, prefix + name + "/")
                else:
                    rows.append((prefix + name, "❌ 空目录", 0))
                continue
            mark = "✅" if len(text) <= limit else "⚠️ 超长（已截断）"
            rows.append((prefix + name, mark, len(text)))
        elif name.endswith((".txt", ".md")) and not name.startswith("_"):
            text = P._read_text(full)
            rows.append((prefix + name, "✅ 单文件卡", len(text)))
    if not rows:
        print("  （空）")
    for name, mark, n in rows:
        print("  %-28s %-42s %6d 字" % (name, mark, n))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", action="append", default=[],
                    help="额外检查的卡目录（可多次给）")
    a = ap.parse_args()

    print("=== 设置 ===")
    st = P.load_settings()
    print("  卡片目录：%s" % " | ".join(P.card_dirs()))
    print("  梗模式：%s   示例对话：%s   上限：%d 字"
          % (st.get("meme_mode"), st.get("include_examples"), st.get("max_chars") or 12000))

    bad = 0
    for d in P.card_dirs() + [os.path.expanduser(x) for x in a.dir]:
        if not os.path.isdir(d):
            print("\n=== %s（不存在，跳过）===" % d)
            continue
        print("\n=== %s ===" % d)
        bad += check_dir(d)
    print()
    if bad:
        print("有 %d 张卡缺正文，需要补。" % bad)
        return 1
    print("全部卡都能用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
