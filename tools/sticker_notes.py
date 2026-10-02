"""给表情库里的表情批量写"用途式短备注"（DeepSeek 看图，不用 GLM）。

- 空备注 / 模糊备注（"卡通形象""一个有趣的表情包"）/ 过长的识图描述 → 重写成 ≤14 字的用法备注
- 看着像私人照片的（自拍/私人/照片）不动，只列出来让你决定
用法：python3 tools/sticker_notes.py [--dry]
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import stickers          # noqa: E402
from agent import vision  # noqa: E402

DRY = "--dry" in sys.argv
REDO = "--redo" in sys.argv
PHOTO_WORDS = ("自拍", "私人", "照片", "个人")


def looks_like_photo(d):
    return any(w in (d or "") for w in PHOTO_WORDS)


def needs_note(d):
    d = (d or "").strip()
    if not d:
        return True
    if len(d) > 18:
        return True
    if "表情包" in d and len(d) <= 12:
        return True
    if d in ("卡通形象", "一个有趣的表情包", "卡通人物表情包"):
        return True
    return False


Q = ("看这张表情包图，只输出一行，不要任何解释：备注|标签1,标签2,标签3\n"
     "备注 ≤14 个汉字，写「什么场合用 / 表达什么」；标签 3 个常用词。\n"
     "例：竖起大拇指点赞|点赞,赞成,牛" + chr(10) + "无语摊手|无语,无奈,摊手")

BAD = ("用户", "分析", "我们", "The user", "the user", "image", "Image", "I need",
       "Let me", "标签1", "备注：", "Format", "输出")


def valid_note(n):
    n = (n or "").strip()
    if not n or len(n) > 14:
        return False
    if any(b in n for b in BAD):
        return False
    return any("\u4e00" <= ch <= "\u9fff" for ch in n)

items = stickers.load()
print("库里共 %d 张" % len(items))
photos, todo, kept = [], [], 0
for it in items:
    d = str(it.get("desc") or "")
    if looks_like_photo(d):
        photos.append((it.get("id"), d))
    elif REDO or needs_note(d):
        todo.append(it)
    else:
        kept += 1
print("要重写: %d 张 | 保留现成短备注: %d 张 | 疑似私人照片(不动): %d 张" % (len(todo), kept, len(photos)))
for pid, pd in photos:
    print("   ⚠️ 疑似私人照片: %s %r" % (pid, pd))
if DRY:
    raise SystemExit

ok = fail = 0
for i, it in enumerate(todo, 1):
    try:
        p = stickers.abs_path(it)
        if not p or not os.path.isfile(p):
            try:                                   # URL-only 的条目：先落盘再喂给模型
                p = stickers.ensure_local(it)
            except Exception:
                p = ""
        if not p or not os.path.isfile(p):
            print("  [%d/%d] %s 图不在本地，跳过" % (i, len(todo), it.get("id")))
            fail += 1
            continue
        raw = vision.describe([p], None, Q, 700)
        if not valid_note(str(raw or "").splitlines()[0].split("|")[0] if raw else ""):
            raw = vision.describe([p], None, Q + chr(10) + "只输出那一行结果本身。", 800)
        note, tags = "", []
        for line in str(raw or "").splitlines():
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                a, b = line.split("|", 1)
                note = a.strip()[:14]
                tags = [t.strip()[:8] for t in re.split(r"[,，、/]", b) if t.strip()][:4]
            else:
                note = line[:14]
            break
        if not valid_note(note):
            print("  [%d/%d] %s 生成失败" % (i, len(todo), it.get("id")))
            fail += 1
            continue
        stickers.set_note(it.get("id"), note, tags or None)
        print("  [%d/%d] %-18s → %s %s" % (i, len(todo), it.get("id"), note, tags))
        ok += 1
    except Exception as e:
        print("  [%d/%d] %s 出错 %r" % (i, len(todo), it.get("id"), e))
        fail += 1
print("\n完成：成功 %d，失败 %d" % (ok, fail))
