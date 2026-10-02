"""把网络上的海龟汤题库导进来（只留下审稿合格的），生成 data/games/turtle_soup/web_puzzles.json。

来源：
  - ModelScope `Narcissuses/Turtle-Bench`（train_8k.json + test_1.5k.json，Apache-2.0）
  - GitHub `KONpiGG/astrbot_plugin_soupai` 的 network_soupai.json（289 道，AGPL-3.0）
  - GitHub `anchorAnc/astrbot_plugin_TurtleSoup` 的 questions_database.txt（41 道，AGPL-3.0）

流水线：下载(缓存) → 繁转简 → 规则清洗 → 去重（对现有题库 + 彼此）→ AI 批量审稿+分类 → 入库。

用法（在插件目录下跑，用 astrbot 的 venv python 才有 zhconv）：
  python3 turtle/import_web.py --fetch              # 只下载缓存
  python3 turtle/import_web.py --dry --limit 200    # 试跑 200 条，不写文件
  python3 turtle/import_web.py --limit 1200 --budget 400000
  python3 turtle/import_web.py --stats              # 看现在的导入结果
  python3 turtle/import_web.py --purge              # 清空导入的题库
"""
import argparse
import difflib
import json
import os
import random
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(os.path.dirname(HERE))
DATA_DIR = os.path.join(PLUGIN_DIR, "data", "games", "turtle_soup")
CACHE_DIR = os.path.join(DATA_DIR, "_web_cache")
WEB_FILE = os.path.join(DATA_DIR, "web_puzzles.json")

sys.path.insert(0, PLUGIN_DIR)
from games.turtle_soup import puzzles as curated        # noqa: E402
from memory import llm                       # noqa: E402

SOURCES = [
    ("ms_test", "https://www.modelscope.cn/datasets/Narcissuses/Turtle-Bench/resolve/master/test_1.5k.json"),
    ("ms_train", "https://www.modelscope.cn/datasets/Narcissuses/Turtle-Bench/resolve/master/train_8k.json"),
    ("soupai", "https://raw.githubusercontent.com/KONpiGG/astrbot_plugin_soupai/master/network_soupai.json"),
    ("anchor", "https://raw.githubusercontent.com/anchorAnc/astrbot_plugin_TurtleSoup/master/questions_database.txt"),
]

# 明显不合适的内容直接丢（血腥猎奇/黄暴/毒品）
BANNED = ("肢解", "碎尸", "分尸", "奸", "轮奸", "虐杀", "娈童", "恋尸", "吃人肉", "剖腹", "挖出内脏",
          "自慰", "性交", "做爱", "阴茎", "阴道", "冰毒", "海洛因", "制毒", "吸毒", "裸体照片")

REVIEW_SYS = """你是海龟汤题库的严格审稿人。下面每道题给你【汤面】和【汤底】，逐条核对：

1. 逻辑自洽：汤底能不能**唯一**解释汤面的反常？有没有靠巧合、需要读者自己补假设？
2. 汤面不要有跟真相无关的误导性废话。
3. 汤面 20~150 字、汤底 30~200 字（超一点可以接受，太短/太长扣分）。
4. 汤底不含糊：不能出现"可能/也许/大概"这类不确定说法。
5. 内容健康：不血腥猎奇、不涉黄涉毒。
6. 是不是"完整的故事"：汤面讲清了一个反常现象，汤底给出了答案（残缺、只有标题、需要额外背景的直接判不合格）。
7. 顺便按"玩家玩完的主要感受"分类，只能选一个：%s
   判断要点：%s
   狠一点：悲伤、意外死亡、生病、纯破案的都**不算**恐怖。

只输出 JSON（不要解释、不要 Markdown）：
{"r": {"0": {"ok": true/false, "score": 0~100, "cat": "类别id", "why": "≤20字理由"}, "1": {...}}}"""


def _cfg() -> dict:
    try:
        with open(os.path.join(PLUGIN_DIR, "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _provider(cfg: dict) -> dict:
    mem = (cfg.get("memory") or {}).get("provider") or {}
    w = ((cfg.get("turtle") or {}).get("web_import") or {})
    p = {
        "base_url": w.get("base_url") or mem.get("base_url"),
        "model": w.get("model") or mem.get("model"),
        "api_key_source": w.get("api_key_source") or mem.get("api_key_source"),
        "timeout": w.get("timeout", 120),
        "temperature": w.get("temperature", 0.2),
        "max_tokens": w.get("max_tokens", 900),
        "thinking": w.get("thinking", mem.get("thinking", {"type": "enabled"})),
        "reasoning_effort": w.get("reasoning_effort", "low"),
    }
    return {"memory": {"provider": p}}


def _to_simple(s: str) -> str:
    try:
        from zhconv import convert
        return convert(str(s or ""), "zh-cn")
    except Exception:
        return str(s or "")


def _norm(s: str) -> str:
    """去掉空白/标点，用于查重。"""
    return re.sub(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()【】\[\]…—\-～~]", "", str(s or ""))


def _tidy(s: str) -> str:
    return " ".join(str(s or "").replace("　", " ").split()).strip()


def _fetch(name: str, url: str, force: bool = False) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, name + os.path.splitext(url)[1])
    if os.path.exists(path) and not force:
        return path
    print("[fetch] %s ..." % url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (qq_peak_gate import)"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read()
    with open(path, "wb") as f:
        f.write(data)
    print("[fetch] %s → %s (%.1f KB)" % (name, path, len(data) / 1024.0))
    return path


def _parse_ms(path: str) -> list:
    out = []
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        print("[parse] ms 失败 %s: %r", path, e)
        return out
    for x in d or []:
        out.append({"title": _tidy(x.get("title")), "surface": _tidy(x.get("surface")),
                    "bottom": _tidy(x.get("bottom")), "src": "modelscope"})
    return out


def _parse_soupai(path: str) -> list:
    out = []
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return out
    for x in d or []:
        if isinstance(x, dict):
            out.append({"title": _tidy(x.get("title")), "surface": _tidy(x.get("puzzle")),
                        "bottom": _tidy(x.get("answer")), "src": "soupai"})
    return out


def _parse_anchor(path: str) -> list:
    txt = open(path, encoding="utf-8", errors="ignore").read()
    out = []
    for block in re.split(r"\n-{3,}\n", txt):
        if "汤面" not in block or "汤底" not in block:
            continue
        def grab(key):
            m = re.search(r"^%s[:：]\s*(.+?)(?=\n[A-Za-z\u4e00-\u9fa5]{2,6}[:：]|\Z)" % key, block,
                          re.M | re.S)
            return _tidy(m.group(1)) if m else ""
        out.append({"title": grab("标题"), "surface": grab("汤面"), "bottom": grab("汤底"),
                    "src": "anchor"})
    return out


def load_sources(force: bool = False) -> list:
    items = []
    parsers = {"ms": _parse_ms, "soupai": _parse_soupai, "anchor": _parse_anchor}
    for name, url in SOURCES:
        try:
            p = _fetch(name, url, force)
        except Exception as e:
            print("[fetch] %s 失败：%r" % (name, e))
            continue
        key = "ms" if name.startswith("ms") else name
        items += parsers[key](p)
    print("[load] 原始 %d 条" % len(items))
    return items


def rule_filter(items: list) -> list:
    out, seen, drop = [], set(), {}
    for x in items:
        sf, bt = _to_simple(x["surface"]), _to_simple(x["bottom"])
        sf, bt = _tidy(sf), _tidy(bt)
        why = ""
        if not sf or not bt:
            why = "缺汤面/汤底"
        elif not (15 <= len(sf) <= 220):
            why = "汤面长度"
        elif not (20 <= len(bt) <= 320):
            why = "汤底长度"
        elif re.search(r"[\[\]【】]|http|www\.|公众号|转载|侵权|图片", sf + bt):
            why = "夹带推广/占位符"
        elif any(b in sf + bt for b in BANNED):
            why = "内容不宜"
        else:
            k = _norm(sf)
            if not k or k in seen:
                why = "重复"
            else:
                seen.add(k)
                x = dict(x, title=_tidy(_to_simple(x.get("title", ""))), surface=sf, bottom=bt)
                out.append(x)
        if why:
            drop[why] = drop.get(why, 0) + 1
    print("[rule] 通过 %d 条，丢掉 %d 条 %s" % (len(out), sum(drop.values()), drop))
    return out


def dedup(items: list, threshold: float = 0.72) -> list:
    """跟现有题库（人工+AI）+ 彼此查重（字面相似度过高就丢）。"""
    base = [(_norm(p.get("surface", ""))) for p in curated.all_puzzles()]
    out = []
    for x in items:
        s = _norm(x["surface"])
        hit = ""
        for b in base:
            if not b:
                continue
            if abs(len(b) - len(s)) > max(20, 0.5 * len(s)):
                continue
            if difflib.SequenceMatcher(None, s, b).ratio() >= threshold:
                hit = b[:20]
                break
        if hit:
            continue
        base.append(s)
        out.append(x)
    print("[dedup] 剩 %d 条" % len(out))
    return out


def review(cfg: dict, items: list, batch: int = 8, budget: int = 400000, dry: bool = False) -> list:
    menu = "、".join("%s(%s)" % (k, v["label"]) for k, v in curated.CATEGORIES.items())
    desc = "；".join("%s=%s" % (k, v.get("desc", "")) for k, v in curated.CATEGORIES.items())
    sys_p = REVIEW_SYS % (menu, desc)
    out, spent, i = [], 0, 0
    while i < len(items):
        chunk = items[i:i + batch]
        lines = []
        for j, x in enumerate(chunk):
            t = ("《%s》" % x["title"]) if x.get("title") else ""
            lines.append("%d) %s汤面：%s\n   汤底：%s" % (j, t, x["surface"], x["bottom"]))
        try:
            txt, u = llm.chat([{"role": "system", "content": sys_p},
                               {"role": "user", "content": "\n".join(lines)}],
                              _provider(cfg), json_mode=True)
        except Exception as e:
            print("[review] 调用失败：%r（这批跳过）" % (e,))
            i += batch
            continue
        spent += int((u or {}).get("total_tokens") or 0)
        m = re.search(r"\{.*\}", txt or "", re.S)
        r = {}
        if m:
            try:
                r = (json.loads(m.group(0)) or {}).get("r") or {}
            except Exception:
                r = {}
        for j, x in enumerate(chunk):
            v = r.get(str(j)) or {}
            try:
                score = int(v.get("score") or 0)
            except Exception:
                score = 0
            cat = str(v.get("cat") or "")
            if v.get("ok") and score >= 75 and cat in curated.CATEGORIES:
                x = dict(x, category=cat, score=score, why=_tidy(v.get("why"))[:40])
                out.append(x)
        print("[review] %d/%d → 通过 %d，花 %d token" % (min(i + batch, len(items)), len(items),
                                                        len(out), spent))
        i += batch
        if budget and spent >= budget:
            print("[review] 到预算了（%d token），先停" % spent)
            break
    print("[review] 共通过 %d 条，用掉 %d token" % (len(out), spent))
    return out


def save_web(items: list, extra: dict = None) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    d = {"updated_at": int(time.time()), "items": items}
    d.update(extra or {})
    tmp = WEB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, WEB_FILE)


def load_web() -> list:
    try:
        with open(WEB_FILE, encoding="utf-8") as f:
            return list((json.load(f) or {}).get("items") or [])
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="只下载缓存")
    ap.add_argument("--force-fetch", action="store_true", help="重新下载")
    ap.add_argument("--limit", type=int, default=0, help="最多审多少条（0=全部）")
    ap.add_argument("--budget", type=int, default=400000, help="本次最多花多少 token")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--purge", action="store_true")
    a = ap.parse_args()

    if a.purge:
        try:
            os.remove(WEB_FILE)
        except Exception:
            pass
        print("已清空导入题库")
        return 0
    if a.stats:
        items = load_web()
        from collections import Counter
        c = Counter(x.get("category") for x in items)
        print("导入题库 %d 道：" % len(items),
              "、".join("%s %d" % (curated.cat_label(k), v) for k, v in c.most_common()))
        print("来源：", Counter(x.get("src") for x in items).most_common())
        print("现有总题库（人工+AI+导入）：", len(curated.all_puzzles()))
        return 0

    cfg = _cfg()
    raw = load_sources(a.force_fetch)
    if a.fetch:
        return 0
    items = dedup(rule_filter(raw))
    random.seed(20261001)
    random.shuffle(items)
    if a.limit:
        items = items[:a.limit]
    print("[main] 本次送审 %d 条" % len(items))
    ok = review(cfg, items, batch=a.batch, budget=a.budget, dry=a.dry)
    if a.dry:
        return 0
    # 续着补：保留原有条目，避免重复导入
    old = load_web()
    ids = {str(x.get("id")) for x in old}
    n = 1
    out = list(old)
    for x in ok:
        while ("web-%d" % n) in ids:
            n += 1
        x["id"] = "web-%d" % n
        x["created_at"] = int(time.time())
        ids.add(x["id"])
        out.append(x)
    save_web(out)
    from collections import Counter
    print("[main] 导入完成：新增 %d 条，库里共 %d 条 %s"
          % (len(ok), len(out), Counter(x.get("category") for x in out).most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
