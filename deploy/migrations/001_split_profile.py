#!/usr/bin/env python3
"""把旧 profile.md 拆成 rules.md（手写规矩）+ impression.md（自动群印象）。

背景：旧文件是"AUTO 段（自动生成，会整体替换）+ 手写补充"混在一起。
新策略（2026-10-22 定）：
  rules.md        手写规矩，全量注入，永不截断
  impression.md   自动印象，注入时截断到 400 字，以后由后台任务重写成 ≤300 字

用法：python3 001_split_profile.py [--data-root /opt/deepsea/data/memory] [--dry]
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

AUTO_RE = re.compile(r"<!--AUTO-BEGIN.*?-->(.*?)<!--AUTO-END-->", re.S)
HAND_RE = re.compile(r"##\s*手写补充[^\n]*\n(.*)", re.S)
IMPression_CAP = 400


def split(text: str) -> tuple[str, str]:
    auto = ""
    m = AUTO_RE.search(text)
    if m:
        auto = m.group(1).strip()
    hand = ""
    m2 = HAND_RE.search(text)
    if m2:
        hand = m2.group(1).strip()
    elif not auto:
        hand = text.strip()          # 没有标记：整篇当手写规矩
    return hand, auto


def compress_impression(auto: str, cap: int = IMPression_CAP) -> str:
    """只保留第一段（群在聊什么）到 cap 字，去掉元信息行。"""
    lines = [ln.strip() for ln in auto.splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.startswith(">")]      # 去掉 glm-… token 统计
    body = "\n".join(lines)
    if len(body) <= cap:
        return body
    cut = body[:cap]
    for sep in ("。", "；", "\n"):
        pos = cut.rfind(sep)
        if pos > cap * 0.6:
            return cut[:pos + 1]
    return cut


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default="/opt/deepsea/data/memory")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    root = Path(a.data_root) / "scope"
    if not root.is_dir():
        print("没有 scope 目录：%s" % root)
        return 1
    for d in sorted(root.iterdir()):
        prof = d / "profile.md"
        if not prof.is_file():
            continue
        rules_f, imp_f = d / "rules.md", d / "impression.md"
        text = prof.read_text(encoding="utf-8")
        hand, auto = split(text)
        imp = compress_impression(auto)
        print("%-16s 规矩 %4d 字 | 印象 %4d 字（原 %d）" %
              (d.name, len(hand), len(imp), len(text)))
        if a.dry:
            continue
        if hand and not rules_f.exists():
            rules_f.write_text(hand.rstrip() + "\n", encoding="utf-8")
        if imp and not imp_f.exists():
            imp_f.write_text(imp.rstrip() + "\n", encoding="utf-8")
        legacy = d / "profile.legacy.md"
        if not legacy.exists():
            prof.rename(legacy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
