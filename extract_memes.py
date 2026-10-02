#!/usr/bin/env python3
"""从 chatlog 提炼本群高频词/黑话，写回 memes.md 的自动区块。纯标准库。

质量规则（v2）：
1. 一个词必须被 **≥2 个不同的人**说过才算群黑话（防止某一个人的口头禅污染词库）
2. 更全的停用词（功能词组合、常见寒暄）
3. 例句优先取"不是说话人自己"的上下文
用法: python3 extract_memes.py [--group 869622030] [--days 30] [--top 40] [--min-count 5] [--min-speakers 2]
"""
import argparse, json, os, re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
MEMES = os.path.join(HERE, "memes.md")
CONFIG = os.path.join(HERE, "config.json")
BEGIN, END = "<!--AUTO-BEGIN", "<!--AUTO-END-->"

STOP = set("""我 你 他 她 它 我们 你们 他们 大家 自己 这个 那个 这些 那些 什么 怎么 为什么 哪里 哪个 现在 今天 明天 昨天 时候 时间 可以 应该 需要 知道 觉得 认为 真的 就是 不是 没有 因为 所以 但是 如果 然后 而且 不过 还是 已经 一直 有点 一下 一个 一种 一样 这样 那样 这么 那么 很多 多少 几个 东西 事情 问题 感觉 意思 样子 地方 朋友 同学 老师 咱们
哈哈 哈哈哈 哈哈哈哈 呵呵 嘿嘿 嘻嘻 嗯嗯 哦哦 好的 好滴 收到 谢谢 多谢 感谢 抱歉 对不起 没事 没关系 可以可以 行行行 好好好 知道了 明白了 懂了 我去 我靠 卧槽 天啊 居然 竟然 终于 突然 反正 估计 可能 大概 也许 其实 确实 的确 真是 好像 似乎
的话 了吧 了吗 呢吗 起来 出来 上来 下来 过去 比如 或者
里面 外面 上面 下面 中间 旁边 前面 后面 左边 右边 的时候 这个是 那个是 就是说 意思是
一个 两个 几个 一点 一些 不是 也是 都是 又是 像是 只是 倒是 真的是 应该是
不要 不能 不用 不会 有没有 是不是 好不好 行不行 能不能 对不对 别的 其他 其它 第一 第二
最后 接着 刚刚 刚才 马上 一会儿 好久 准备 打算 目前 之前 之后 之后
我这 你这 他这 这边 那边 哪边 这里 那里 哪里 怎么办 怎么样
消息 免费一天 模型 总结 注意 发送 配置 服务器 机器人 大肥鱼 小鲸鱼 蓝色邪恶 群里 群聊""".split())

CJK = r"\u4e00-\u9fa5"
LATIN = r"A-Za-z"
EMOJI = r"\u2600-\u27bf\U0001f300-\U0001faff"
CJK_RE = re.compile(r"[%s]" % CJK)


def norm(t: str) -> str:
    t = re.sub(r"\[CQ:[^\]]*\]", " ", t or "")
    t = re.sub(r"https?://\S+", " ", t)
    return t


def candidates(text: str) -> set:
    """这条消息里的候选词：英文词、中文 2~4 字 n-gram、中英混排词。"""
    found = set()
    for mt in re.finditer(r"[A-Za-z]{2,}|\d{3,}", text):
        found.add(mt.group(0).lower())
    for mt in re.finditer(r"[A-Za-z]{2,}", text):
        i = mt.start(); j = i
        while j > 0 and CJK_RE.match(text[j - 1]) and i - j < 4:
            j -= 1
        for k in range(j, i):
            piece = text[k:i] + mt.group(0)
            if len(piece) >= 3:
                found.add(piece)
    for run in re.finditer(r"[%s]{2,}" % CJK, text):
        s = run.group(0)
        for n in range(2, 13):
            for i in range(len(s) - n + 1):
                found.add(s[i:i + n])
    return found


def build(gid=None, days=30, top=40, min_count=5, min_speakers=2):
    d = os.path.join(HERE, "chatlog")
    if not os.path.isdir(d):
        print("没有 chatlog 目录"); return []
    files = sorted(f for f in os.listdir(d) if f.endswith(".jsonl"))[-days:]
    msgs = []                                    # (text, uid)
    for f in files:
        try:
            for line in open(os.path.join(d, f), encoding="utf-8"):
                r = json.loads(line)
                if gid and str(r.get("g")) != str(gid):
                    continue
                t = norm(r.get("x", ""))
                if t.strip():
                    msgs.append((t, str(r.get("u") or "")))
        except Exception:
            continue
    if len(msgs) < 30:
        print("样本太少（%d 条），先攒攒" % len(msgs)); return []

    cnt, ex, speakers = Counter(), {}, defaultdict(set)
    for t, uid in msgs:
        for w in candidates(t):
            cnt[w] += 1
            speakers[w].add(uid)
            if w not in ex:
                ex[w] = t
    total = len(msgs)
    out = []
    for w, c in cnt.items():
        if c < min_count or len(w) < 2:
            continue
        if w in STOP or w.lower() in STOP:
            continue
        if c > total * 0.35:
            continue
        if len(speakers[w]) < min_speakers:      # ★ 核心：至少几个人说过
            continue
        if re.fullmatch(r"[%s%s%s]+" % (CJK, LATIN, EMOJI), w) is None:
            continue
        out.append((w, c, len(speakers[w])))
    out.sort(key=lambda x: (-x[1], -len(x[0])))
    keep, seen = [], set()
    for w, c, sp in out:                         # 抑制子串
        if any(w != k and w in k and cnt[k] >= c * 0.7 for k in seen):
            continue
        keep.append((w, c, sp))
        seen.add(w)
        if len(keep) >= top:
            break
    return [(w, c, ex.get(w, ""), sp) for w, c, sp in keep]


def write_block(rows):
    block = [BEGIN + " 由 extract_memes.py 自动生成，下次运行整体替换，可自由删改 -->",
             "【本群高频词/黑话（自动提取，参考用，不要刻意堆砌；别人用你接得住就行）】"]
    for w, c, e, sp in rows:
        e = (e or "").strip()
        if len(e) > 24:
            e = e[:24] + "…"
        block.append("- %s%s" % (w, ("（例：%s）" % e) if e and e != w else ""))
    block.append(END)
    return "\n".join(block)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default=None)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--min-count", type=int, default=5)
    ap.add_argument("--min-speakers", type=int, default=2)
    ap.add_argument("--prune-days", type=int, default=60)
    a = ap.parse_args()
    gid = a.group
    if not gid and os.path.exists(CONFIG):
        try:
            gid = json.load(open(CONFIG, encoding="utf-8")).get("memes_group") or None
        except Exception:
            pass
    rows = build(gid, a.days, a.top, a.min_count, a.min_speakers)
    if not rows:
        print("无输出（样本不足或没有高频词），memes.md 不变"); return
    s = open(MEMES, encoding="utf-8").read() if os.path.exists(MEMES) else ""
    new = write_block(rows)
    if BEGIN in s and END in s:
        s = s[:s.index(BEGIN)] + new + s[s.index(END) + len(END):]
    else:
        s = s.rstrip() + "\n\n" + new + "\n"
    open(MEMES, "w", encoding="utf-8").write(s)
    print("已更新 memes.md 自动区块：%d 个词（群 %s，要求 ≥%d 人说过）" % (len(rows), gid or "全部", a.min_speakers))
    for w, c, e, sp in rows[:15]:
        print("  %s x%d（%d人）" % (w, c, sp))
    d = os.path.join(HERE, "chatlog")
    cut = datetime.now() - timedelta(days=a.prune_days)
    for f in (os.listdir(d) if os.path.isdir(d) else []):
        try:
            if datetime.strptime(f[:10], "%Y-%m-%d") < cut:
                os.remove(os.path.join(d, f)); print("  删除旧日志 %s" % f)
        except Exception:
            pass


if __name__ == "__main__":
    main()
