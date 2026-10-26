#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 Character Card V2 的 JSON 嵌入一张 PNG，做成 SillyTavern 可直接导入的角色卡。

用法：
    python3 embed_png_card.py <头像.png> [输出.png]

说明：
- 输入 PNG 建议 400x600 或 512x768。
- 数据写入 PNG 的 tEXt 块，关键字为 chara，值为 JSON 的 base64（UTF-8）。
- 不依赖 Pillow，纯标准库实现。
"""
import base64
import json
import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
CARD_JSON = HERE / "skadi-character-card-v2.json"


def chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_name("Skadi-card.png")
    if not src.exists():
        raise SystemExit(f"找不到输入文件：{src}")

    payload = json.loads(CARD_JSON.read_text(encoding="utf-8"))
    b64 = base64.b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    text_chunk = chunk(b"tEXt", b"chara\x00" + b64.encode("ascii"))

    raw = src.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit("输入不是 PNG 文件")
    if b"tEXtchara" in raw:
        raise SystemExit("该 PNG 已经嵌入过 chara 数据")

    # 在 IEND 之前插入 tEXt
    iend = raw.rfind(b"IEND")
    if iend == -1:
        raise SystemExit("PNG 结构异常：找不到 IEND")
    start = iend - 4  # 长度字段
    out = raw[:start] + text_chunk + raw[start:]
    dst.write_bytes(out)
    print(f"已生成：{dst}（{dst.stat().st_size} bytes）")


if __name__ == "__main__":
    main()
