#!/usr/bin/env python3
"""控制台后端自测：只改 __custom__、保注释、校验失败就不写、越权段拒绝。"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from console import config_io  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra[:70]))
    if not cond:
        FAIL.append(name)


SAMPLE = '''data_dir = "data"
owner_ids = [1]

[gate]
default_preset = "strict"          # 没单独配的会话用哪一档
credit_max = 3                     # 发言额度
score_threshold = 2.0

[turn]
max_rounds = 8

[adapters.onebot]
enabled = true
'''


def main() -> int:
    td = Path(tempfile.mkdtemp())
    cfg = td / "core__custom__.toml"
    cfg.write_text(SAMPLE, encoding="utf-8")

    print("== 读取 ==")
    view = config_io.read_core(cfg)
    ck("有 6 个核心段", set(view["sections"]) == set(config_io.EDITABLE_SECTIONS),
       str(list(view["sections"])))
    gate = {f["key"]: f["value"] for f in view["sections"]["gate"]["fields"]}
    ck("读到了现值", gate.get("credit_max") == 3 and gate.get("default_preset") == "strict",
       str(gate)[:50])
    ck("元信息可写", view["meta"]["writable"] is True, str(view["meta"]))

    print()
    print("== 写入：保留注释、只动指定的键 ==")
    before = cfg.read_text(encoding="utf-8")
    applied = config_io.apply_changes(cfg, {"gate": {"credit_max": 5.0}})
    after = cfg.read_text(encoding="utf-8")
    ck("返回了新旧值", applied["gate"]["credit_max"] == [3, 5.0], str(applied))
    ck("注释还在", "# 没单独配的会话用哪一档" in after and "# 发言额度" in after, "")
    ck("别的键没被改", 'default_preset = "strict"' in after, "")
    ck("值真的变了", config_io.load_raw(cfg)["gate"]["credit_max"] == 5.0, "")
    ck("留了备份", len(list(td.glob("core__custom__.toml.bak-*"))) == 1, "")

    print()
    print("== 校验：越界/类型错/未知键一律拒绝且不落盘 ==")
    snapshot = cfg.read_text(encoding="utf-8")
    for bad, why in (
        ({"gate": {"credit_max": -5}}, "负数"),
        ({"gate": {"default_preset": "wild"}}, "非法枚举"),
        ({"gate": {"不存在的键": 1}}, "未知键"),
        ({"turn": {"max_rounds": "abc"}}, "类型错"),
    ):
        try:
            config_io.apply_changes(cfg, bad)
            ck("拒绝 " + why, False, "居然写进去了")
        except config_io.ConfigError:
            ck("拒绝 " + why, True)
    ck("内容没变", cfg.read_text(encoding="utf-8") == snapshot, "")

    print()
    print("== 只允许改 __custom__ 文件 ==")
    plain = td / "core.toml"
    shutil.copy2(cfg, plain)
    try:
        config_io.apply_changes(plain, {"gate": {"credit_max": 4}})
        ck("拒绝改普通配置", False, "居然写进去了")
    except config_io.ConfigError as e:
        ck("拒绝改普通配置", "只允许修改" in str(e), str(e)[:40])

    print()
    print("== 不允许的段 ==")
    try:
        config_io.apply_changes(cfg, {"adapters.onebot": {"enabled": False}})
        ck("拒绝通道段（那是通道层的事）", False, "居然写进去了")
    except config_io.ConfigError as e:
        ck("拒绝通道段（那是通道层的事）", "不允许" in str(e), str(e)[:40])

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
