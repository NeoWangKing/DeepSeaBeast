"""《明日方舟》资料每月刷新：抓官网新闻 → 让模型归纳成"最新动态" → 写进资料库并重建索引。

- 只读官网公开页面，不改动游戏相关内容以外的任何东西
- 抓不到 / 模型失败 → 保留旧文件，只记日志（绝不写坏数据）
- 用法：python3 tools/arknights_refresh.py [--dry]
"""
import json
import os
import re
import sys
import time
import urllib.request
import html as _html

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import kb                      # noqa: E402
from agent import llm as agent_llm   # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
OUT = os.path.join(HERE, "data", "kb", "arknights-news.md")
LOG = os.path.join(HERE, "data", "kb_refresh.log")
STATUS = os.path.join(HERE, "data", "kb_refresh_status.json")
DRY = "--dry" in sys.argv


def log(msg: str) -> None:
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M"), msg)
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def fetch(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def clean(t: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S)
    t = _html.unescape(re.sub(r"<[^>]+>", " ", t))
    return " ".join(t.split())


def main() -> int:
    snippets = []
    try:
        raw = fetch("https://ak.hypergryph.com/news")
        # 官网是 Next.js：新闻数据在 self.__next_f.push([1,"..."]) 里
        parts = re.findall(r"self\.__next_f\.push\(\[\d+,\s*\"(.*?)\"\]\)", raw, re.S)
        blob = " ".join(parts)
        blob = blob.replace(chr(92) + chr(34), chr(34)).replace(chr(92) + "n", " ")
        blob = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), blob)
        # 抽结构化字段：标题 + 简介
        titles = []
        for m in re.finditer(r'"title"\s*:\s*"([^"]{4,140})"', blob):
            t = _html.unescape(m.group(1)).strip()
            if t and t not in [x[0] for x in titles]:
                titles.append((t, ""))
        for i, (t, _b) in enumerate(titles[:16]):
            mi = blob.find('"title":"%s"' % t)
            if mi < 0:
                continue
            mb = re.search(r'"brief"\s*:\s*"([^"]{0,300})"', blob[mi:mi + 2000])
            if mb:
                titles[i] = (t, _html.unescape(mb.group(1)).strip()[:200])
        snippets = ["%s —— %s" % (t, b) if b else t for t, b in titles[:16]]
        if not snippets:
            log("飞行数据里没抽到标题，退回关键词窗口")
        log("官网抓取成功：%d 条相关片段" % len(snippets))
    except Exception as e:
        log("官网抓取失败：%r" % (e,))
        snippets = []

    if not snippets:
        log("没有可用片段 → 本次不更新（保留旧资料）")
        return 1

    prompt = ("下面是《明日方舟》官网新闻页抓到的片段（可能有噪声）。请**只根据这些内容**，"
              "写一段给「群里聊天用」的《明日方舟》最新动态，要求：\n"
              "- 用 4~8 条短句（每条 ≤40 字），写清楚：当前活动叫什么、什么时候开始/结束、有没有新干员/新时装/复刻、"
              "有什么值得注意的（如危机合约/集成战略）——只写片段里出现过的。\n"
              "- 不确定的一律不写；实在信息太少就写「官网暂未抓到有效信息」。\n"
              "- 不要客套话、不要编造日期或名称。\n\n"
              "【片段】\n" + "\n".join("- " + s for s in snippets)[:4000])

    try:
        body = agent_llm.chat_text([{"role": "user", "content": prompt}], None, 700)
    except Exception as e:
        log("模型归纳失败：%r" % (e,))
        return 1
    _lines = [l.strip(" \t-•*·") for l in str(body or "").splitlines() if l.strip()]
    body = "\n".join("- " + l for l in _lines if l)
    if len(body) < 20:
        log("模型输出太短（%d 字）→ 放弃本次更新" % len(body))
        return 1

    text = ("# 明日方舟 · 最新动态（自动刷新）\n\n"
            "> 由官网新闻自动抓取 + 归纳，**更新于 %s**。老活动/版本会变，聊天时以官方公告为准。\n\n"
            "%s\n\n"
            "> 来源：官方新闻页 https://ak.hypergryph.com/news\n" % (
                time.strftime("%Y-%m-%d %H:%M"), body))
    if DRY:
        log("dry-run，不写文件。预览：\n" + text[:400])
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, OUT)
    try:
        st = kb.build()
        log("已写入 %s，索引重建：%s" % (os.path.basename(OUT), st))
    except Exception as e:
        log("索引重建失败：%r" % (e,))
    try:
        json.dump({"updated": int(time.time()), "source": "ak.hypergryph.com/news",
                   "snippets": len(snippets)},
                  open(STATUS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
