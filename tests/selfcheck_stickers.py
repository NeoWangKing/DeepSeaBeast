#!/usr/bin/env python3
"""贴图自测：不混图片、挑得准、找不到就明说、备注可改。"""

from __future__ import annotations

import json
import os
import shutil
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
    td = Path(tempfile.mkdtemp())
    os.environ["DEEPSEA_PLUGIN_DATA"] = str(td)
    import importlib.util
    spec = importlib.util.spec_from_file_location("stickers", "plugins/stickers/main.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    # 造三张贴图（内容无所谓，占位字节）
    lib = td / "library"
    lib.mkdir(parents=True, exist_ok=True)
    items = [
        {"id": "s1", "file": "a.gif", "desc": "捂脸笑：无语又想笑，别人犯蠢时用",
         "tags": ["无语", "笑哭", "捂脸"], "used": 50, "last_used": 0, "group": "g1"},
        {"id": "s2", "file": "b.jpg", "desc": "点赞：竖大拇指，表示肯定/牛逼",
         "tags": ["点赞", "肯定", "牛逼"], "used": 3, "last_used": 0, "group": "g1"},
        {"id": "s3", "file": "c.png", "desc": "狗头：开玩笑的护体符，说完损人话加上它",
         "tags": ["狗头", "保命", "玩笑"], "used": 10, "last_used": 0, "group": "g1"},
    ]
    for it in items:
        (lib / it["file"]).write_bytes(b"x")
    (td / "index.json").write_text(json.dumps({"items": items}, ensure_ascii=False),
                                   encoding="utf-8")

    print("== 挑得准 ==")
    r = m.do_send_sticker({"query": "无语"})
    ck("按「无语」挑到捂脸笑", r["ok"] and r["data"]["send"][0]["id"] == "s1", str(r)[:80])
    r = m.do_send_sticker({"query": "夸他牛逼"})
    ck("按「牛逼」挑到点赞", r["ok"] and r["data"]["send"][0]["id"] == "s2", str(r)[:80])
    r = m.do_send_sticker({"query": "骂完人保命"})
    ck("按「保命」挑到狗头", r["ok"] and r["data"]["send"][0]["id"] == "s3", str(r)[:80])

    print()
    print("== 发出去的是 sticker，不是 image ==")
    kind = r["data"]["send"][0]["kind"]
    ck("意图类型是 sticker", kind == "sticker", kind)

    print()
    print("== 找不到就明说，不乱发 ==")
    r = m.do_send_sticker({"query": "量子力学"})
    ck("没匹配时 ok=False", r.get("ok") is False, str(r)[:60])
    ck("错误信息说明原因", "没找到" in str(r.get("error")), str(r.get("error"))[:40])

    print()
    print("== 用一次记一次 ==")
    idx = json.loads((td / "index.json").read_text(encoding="utf-8"))
    s1 = next(x for x in idx["items"] if x["id"] == "s1")
    ck("s1 使用次数 +1", s1["used"] == 51, str(s1["used"]))
    ck("记了最近使用时间", s1["last_used"] > 0)

    print()
    print("== 备注/分类可改 ==")
    r = m.do_note_sticker({"id": "s2", "desc": "点赞：表示认可", "tags": ["点赞", "认可"]})
    ck("note 成功", r.get("ok") is True, str(r)[:40])
    idx = json.loads((td / "index.json").read_text(encoding="utf-8"))
    s2 = next(x for x in idx["items"] if x["id"] == "s2")
    ck("描述已更新", s2["desc"] == "点赞：表示认可", s2["desc"])
    ck("标签已更新", s2["tags"] == ["点赞", "认可"], str(s2["tags"]))

    print()
    print("== 文件丢了的条目不参与挑选 ==")
    (lib / "c.png").unlink()
    r = m.do_send_sticker({"query": "狗头"})
    ck("文件不在就挑不到", r.get("ok") is False, str(r)[:50])

    print()
    print("== 清单 ==")
    r = m.do_list_stickers({"keyword": "点赞"})
    ck("按关键词能列出来", "点赞" in r["data"]["text"], r["data"]["text"][:50])

    print()
    print("== 自动收集：群里别人发的贴图收进库（待打标）==")
    import http.server, threading
    payload = b"GIF89a" + b"x" * 40          # 假装是一张 GIF

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/sticker.gif" % srv.server_address[1]
    before = len(json.loads((td / "index.json").read_text(encoding="utf-8"))["items"])
    m.on_event("message.received", {"scope": {"ref": "g1"},
                                    "media": [{"kind": "sticker", "url": url, "name": "狗头"}]})
    after_items = json.loads((td / "index.json").read_text(encoding="utf-8"))["items"]
    ck("收进来一条", len(after_items) == before + 1, "%d → %d" % (before, len(after_items)))
    newbie = after_items[-1]
    ck("标成待打标", newbie.get("status") == "pending" and not newbie.get("desc"),
       str(newbie)[:70])
    ck("文件真的落盘了", (lib / newbie["file"]).exists(), newbie["file"])
    ck("记了 md5 防重复", bool(newbie.get("md5")), str(newbie.get("md5"))[:12])

    print()
    print("== 同一张不重复收 ==")
    m.on_event("message.received", {"scope": {"ref": "g1"},
                                    "media": [{"kind": "sticker", "url": url, "name": "狗头"}]})
    again = json.loads((td / "index.json").read_text(encoding="utf-8"))["items"]
    ck("重复的不再收", len(again) == len(after_items), "%d → %d" % (len(after_items), len(again)))

    print()
    print("== 待打标的不会被挑中（还没描述就发不出去）==")
    r2 = m.do_send_sticker({"query": "狗头"})
    ck("没打标的挑不到", r2.get("ok") is False, str(r2)[:60])
    srv.shutdown()

    print()
    print("== 核心把贴图媒体信息交给插件 ==")
    from core.runtime import _media_of
    from core.channel import ChatRef, InboundMessage, Message, Segment, Sender

    class _M:
        segments = [Segment("text", text="看这个"),
                    Segment("sticker", text="[贴图:狗头]",
                            data={"name": "狗头", "url": "http://x/y.gif", "file": ""}),
                    Segment("image", text="[图片]", data={"url": "http://x/z.jpg"})]

    class _Msg:
        message = _M()

    media = _media_of(_Msg())
    ck("贴图进了清单", any(x["kind"] == "sticker" and x["url"] for x in media), str(media))
    ck("图片也进了清单", any(x["kind"] == "image" for x in media), str(media))
    ck("文本段不在清单里", all(x["kind"] != "text" for x in media))

    print()
    print("== 核心词汇表里有 sticker，且不等于 image ==")
    from core.capability import ALL_VERBS, KIND_TO_VERB
    ck("sticker 是独立动词", "sticker" in ALL_VERBS and KIND_TO_VERB["sticker"] == "sticker")

    shutil.rmtree(td, ignore_errors=True)
    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
