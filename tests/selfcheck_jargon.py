#!/usr/bin/env python3
"""黑话/表达库自测：命中才注入、不命中的不说、教了能记住。"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


def main() -> int:
    td = tempfile.mkdtemp()
    os.environ["DEEPSEA_PLUGIN_DATA"] = td
    spec = importlib.util.spec_from_file_location("jargon", "plugins/jargon/main.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    scope = "s_test"

    print("== 教她一个说法 ==")
    r = m.do_learn_term(scope, {"term": "玉湖", "meaning": "某个人的简称"})
    ck("记住了", r.get("ok") is True, str(r)[:60])
    d = json.load(open(os.path.join(td, "s_test.json"), encoding="utf-8"))
    ck("落盘了", len(d["terms"]) == 1, str(d)[:60])

    print()
    print("== 命中才注入 ==")
    k = m.explain(scope, "玉湖又断网了")
    ck("提到了就解释", any("玉湖" in x and "简称" in x for x in k), str(k))
    k2 = m.explain(scope, "今天天气不错")
    ck("没提到就一个字不给", k2 == [], str(k2))

    print()
    print("== 句式：占位符当通配 ==")
    m.load(scope)["patterns"].append(
        {"surface": "已完成今日{名词}大学习", "note": "打卡腔，模仿口号"})
    d = m.load(scope)
    d["patterns"].append({"surface": "已完成今日{名词}大学习", "note": "打卡腔，模仿口号"})
    m.save(scope, d)
    k3 = m.explain(scope, "已完成今日325大学习")
    ck("句式命中", any("大学习" in x and "打卡腔" in x for x in k3), str(k3))
    k4 = m.explain(scope, "完全无关的一句话")
    ck("无关的不命中", k4 == [], str(k4))

    print()
    print("== 一次最多给 3 条 ==")
    d = m.load(scope)
    for i in range(5):
        d["terms"].append({"term": "词%d" % i, "meaning": "解释%d" % i})
    m.save(scope, d)
    k5 = m.explain(scope, "词0 词1 词2 词3 词4")
    ck("最多 3 条", len(k5) == 3, str(len(k5)))

    print()
    print("== 会话之间隔离 ==")
    k6 = m.explain("s_other", "玉湖又断网了")
    ck("别的群不知道这个说法", k6 == [], str(k6))

    print()
    print("== 忘掉 ==")
    r = m.do_forget_term(scope, {"term": "玉湖"})
    ck("能忘掉", r.get("ok") is True, str(r)[:40])
    ck("忘掉后不再解释", m.explain(scope, "玉湖又断网了") == [])

    print()
    print("== 工具声明 ==")
    names = {t["name"] for t in m.TOOLS}
    ck("三个工具齐全", names == {"learn_term", "list_terms", "forget_term"}, str(names))

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
