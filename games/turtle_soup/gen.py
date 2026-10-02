"""让 AI 自己编海龟汤：生成 → 自评 → 查重 → 入库（用量很小）。

为什么能便宜：只调 flashx 档的小模型、每次只要几十~一百多 token，且是**离线批处理**
（跟聊天回复完全无关）。一道题大约 1.5k~2k token，一天补 2 道 ≈ 4k token；300 万 token
的包能顶两年。生成的题跟人工题库一起参与「同群一年不重出」的冷却。

用法（在插件目录下）：
  python3 turtle/gen.py --n 5          # 生成 5 道（自评合格才入库）
  python3 turtle/gen.py --topup        # 把题库补到 target（config.turtle.gen.target）
  python3 turtle/gen.py --list         # 列出现有 AI 题
  python3 turtle/gen.py --check ai-3   # 单独复查某道题
  python3 turtle/gen.py --drop ai-3    # 删掉某道题
  python3 turtle/gen.py --n 3 --dry    # 只打印，不写文件

退出码 0 = 正常；1 = 跑到一半出错了（已生成的仍然保留）。
"""
import argparse
import difflib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(os.path.dirname(HERE))
DATA_DIR = os.path.join(PLUGIN_DIR, "data", "games", "turtle_soup")
AI_FILE = os.path.join(DATA_DIR, "ai_puzzles.json")

sys.path.insert(0, PLUGIN_DIR)
from memory import llm  # noqa: E402
from games.turtle_soup import puzzles as curated  # noqa: E402

# ---------- 出题规则（跟人工题库同一套标准）----------
CATEGORY_DESC = {k: "%s：%s" % (v["label"], v.get("desc", "")) for k, v in curated.CATEGORIES.items()}


def _cat_line(category) -> str:
    d = CATEGORY_DESC.get(str(category or ""))
    if not d:
        return ""
    name, desc = d.split("：", 1)
    return "这道题必须是「%s」类：%s。" % (name, desc)


GEN_SYS = """你是海龟汤（情境推理游戏）出题人，专门写「小而巧」的中文推理题。

硬规矩：
1. **逻辑必须自洽**：汤底要能唯一解释汤面里每一个反常之处，不靠巧合、不靠额外假设。
2. **汤面不写误导性废话**：汤面里出现的细节都要跟真相有关，不能为了迷惑人硬塞。
3. **汤面 30~90 字**，只描述"看起来不合理的现象"，不提真相；
   **汤底 50~100 字**，把因果讲清楚，读完要让人「哦——原来如此」。
   写完自己数一遍字数，超了就先删废话再交。
4. 要新的题：可以借鉴经典套路，但不要照抄下面这些已有题；
5. 不写血腥猎奇细节，不写可被模仿的自杀/杀人/危险动作的具体做法。

下面这些题已经存在，**不要重复**：
%s

只输出一个 JSON 对象，不要解释、不要 Markdown：
{"title": "6~12 字标题", "surface": "汤面", "bottom": "汤底"}"""

CRIT_SYS = """你是海龟汤题库的严格审稿人。给你一道新题的汤面和汤底，逐条核对：

1. 汤底能不能**唯一**解释汤面里的反常？有没有靠巧合、有没有需要读者自己补假设？
2. 汤面里有没有跟真相无关的误导性细节？（有就扣分）
3. 汤面 25~120 字、汤底 40~120 字，长度合不合规？
4. 汤底是不是把话说清楚了、没有含糊其辞（不能出现"可能""也许"这种不确定说法）？
5. 内容是否健康：不能有血腥猎奇细节，不能写可被模仿的自杀/杀人/危险动作做法。
6. 标题必须和汤面/汤底呼应，不能风马牛不相及（跑题的标题要扣分）；
7. 跟下面已有题是否高度雷同（换个说法讲同一个梗也算雷同）：
%s

只输出一个 JSON 对象，不要解释、不要 Markdown：
{"score": 0~100 的整数, "ok": true/false, "duplicate": true/false, "reason": "一句话理由"}
ok 只有在 score>=80 且不重复且不误导时才为 true。"""


def _load_cfg() -> dict:
    try:
        with open(os.path.join(PLUGIN_DIR, "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _gen_cfg(cfg: dict) -> dict:
    """出题模型：默认跟记忆系统同一档（GLM flashx），可用 turtle.gen 覆盖。"""
    mem = (cfg.get("memory") or {}).get("provider") or {}
    g = ((cfg.get("turtle") or {}).get("gen") or {})
    p = {
        "base_url": g.get("base_url") or mem.get("base_url"),
        "model": g.get("model") or mem.get("model"),
        "api_key_source": g.get("api_key_source") or mem.get("api_key_source"),
        "timeout": g.get("timeout", 90),
        "temperature": g.get("temperature", 1.0),        # 出题要发散一点
        "max_tokens": g.get("max_tokens", 700),
        "thinking": g.get("thinking", mem.get("thinking", {"type": "enabled"})),
        "reasoning_effort": g.get("reasoning_effort", "low"),
    }
    return {"memory": {"provider": p}}


def load_ai() -> list:
    try:
        with open(AI_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return list(d.get("items") or [])
    except Exception:
        return []


def save_ai(items: list, extra: dict | None = None) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    d = {"updated_at": int(time.time()), "items": items}
    d.update(extra or {})
    tmp = AI_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, AI_FILE)


def all_brief() -> list:
    out = []
    for p in list(curated.PUZZLES) + load_ai():
        out.append("《%s》%s" % (p.get("title", ""), p.get("surface", "")))
    return out


def _json_obj(txt: str) -> dict:
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _similar(a: str, others: list) -> tuple:
    """朴素查重：跟已有汤面/标题的字面相似度（AI 复核之外的第二道闸）。"""
    for o in others:
        r = difflib.SequenceMatcher(None, a, o).ratio()
        if r >= 0.72:
            return True, o[:30]
    return False, ""


def _tidy(s: str) -> str:
    return " ".join(str(s or "").replace("　", " ").split()).strip()


REPAIR_SYS = """你是海龟汤出题人。下面这道题被审稿人退稿了，请你**修好它**：

- 必须解决审稿人指出的问题；
- 保留这道题原有的亮点，不要换成一个完全不同的故事；
- 长度和格式要求同前：汤面 30~90 字、汤底 50~100 字，汤底要把因果说清楚、不含糊；
- 不要写血腥猎奇细节，不要写可被模仿的危险做法。

审稿人意见：%s

只输出一个 JSON 对象，不要解释、不要 Markdown：
{"title": "6~12 字标题", "surface": "汤面", "bottom": "汤底"}"""


def _draft(cfg: dict, briefs: list, budget: dict, repair=None, category=None) -> dict | None:
    """让模型写一版（repair=(题, 退稿理由) 时是回炉改题）。"""
    pcfg = _gen_cfg(cfg)
    _cl = _cat_line(category)
    if repair and repair[0]:
        sys_p = REPAIR_SYS % _tidy(repair[1])[:120]
        if _cl:
            sys_p += "\n\n另外：" + _cl
        user_p = json.dumps(repair[0], ensure_ascii=False)
    else:
        sys_p = GEN_SYS % "\n".join("- " + b for b in briefs[-40:])
        if _cl:
            sys_p += "\n\n" + _cl
        user_p = "出一道新的海龟汤。"
        hint = budget.get("hint") or ""
        if hint:
            user_p += "（上一道被退稿的原因：%s，这次别再犯。）" % hint[:80]
    try:
        txt, u = llm.chat([{"role": "system", "content": sys_p},
                           {"role": "user", "content": user_p}],
                          pcfg, json_mode=True, max_tokens=700)
    except Exception as e:
        print("[gen] 调用模型失败：%r" % (e,))
        return None
    budget["tokens"] += int((u or {}).get("total_tokens") or 0)
    d = _json_obj(txt)
    pz = {"title": _tidy(d.get("title")), "surface": _tidy(d.get("surface")),
          "bottom": _tidy(d.get("bottom"))}
    if not pz["title"]:
        pz["title"] = _tidy(repair[0].get("title")) if (repair and repair[0]) else ""
    return pz


def _check(cfg: dict, briefs: list, pz: dict, budget: dict, category=None) -> dict:
    """验长度 → 查重 → 严审。返回 {ok, puzzle, why, score}。"""
    pcfg = _gen_cfg(cfg)
    title, surface, bottom = pz.get("title", ""), pz.get("surface", ""), pz.get("bottom", "")
    if not (title and surface and bottom):
        return {"ok": False, "puzzle": pz, "why": "模型没按格式给出 title/surface/bottom"}
    if not (25 <= len(surface) <= 120 and 40 <= len(bottom) <= 120):
        # 好题别急着扔：让模型自己压一遍长度（很便宜的一次调用）
        try:
            txt2, u2 = llm.chat(
                [{"role": "system", "content": "把用户给的汤面和汤底改写得更简洁，"
                                               "汤面压到 30~90 字、汤底压到 50~100 字，"
                                               "信息一点都不能丢、逻辑不能变。"
                                               "只输出 JSON：{\"title\":\"\",\"surface\":\"\",\"bottom\":\"\"}"},
                 {"role": "user", "content": json.dumps(pz, ensure_ascii=False)}],
                pcfg, json_mode=True, max_tokens=500)
            budget["tokens"] += int((u2 or {}).get("total_tokens") or 0)
            d2 = _json_obj(txt2)
            if d2:
                pz = {"title": _tidy(d2.get("title")) or title,
                      "surface": _tidy(d2.get("surface")) or surface,
                      "bottom": _tidy(d2.get("bottom")) or bottom}
                title, surface, bottom = pz["title"], pz["surface"], pz["bottom"]
        except Exception:
            pass
    if not (25 <= len(surface) <= 120 and 40 <= len(bottom) <= 120):
        return {"ok": False, "puzzle": pz,
                "why": "长度不合规（汤面 %d 字 / 汤底 %d 字）" % (len(surface), len(bottom))}
    dup, who = _similar(surface, briefs)
    if dup:
        return {"ok": False, "puzzle": pz, "why": "跟已有题字面太像：%s" % who}
    crit_sys = CRIT_SYS % "\n".join("- " + b for b in briefs[-40:])
    _cl = _cat_line(category)
    if _cl:
        crit_sys += "\n\n另外：这道题按要求必须是这个类别——%s 不属于就直接 ok=false。" % _cl
    ctxt, cu = llm.chat([{"role": "system", "content": crit_sys},
                         {"role": "user", "content": "汤面：%s\n汤底：%s" % (surface, bottom)}],
                        pcfg, json_mode=True, max_tokens=300)
    budget["tokens"] += int((cu or {}).get("total_tokens") or 0)
    c = _json_obj(ctxt)
    try:
        score = int(c.get("score") or 0)
    except Exception:
        score = 0
    if not c:
        return {"ok": False, "puzzle": pz, "why": "审稿没返回可解析的 JSON"}
    if not c.get("ok") or score < 78 or c.get("duplicate"):
        return {"ok": False, "puzzle": pz, "score": score,
                "why": "自评没过（%s 分）：%s" % (score, _tidy(c.get("reason"))[:60])}
    pz.update({"score": score, "reason": _tidy(c.get("reason"))[:80]})
    return {"ok": True, "puzzle": pz, "why": "通过（%s 分）" % score}


def _one(cfg: dict, briefs: list, budget: dict, repair=None, category=None) -> dict:
    """写一版 + 验收。repair=(上一版, 退稿理由) 时走回炉改题。"""
    pz = _draft(cfg, briefs, budget, repair=repair, category=category)
    if not pz:
        return {"ok": False, "puzzle": {}, "why": "模型没返回可解析的内容"}
    return _check(cfg, briefs, pz, budget, category=category)


_QUOTA_FILE = os.path.join(DATA_DIR, "_gen_quota.json")


def _quota_take(max_per_day: int) -> bool:
    """每天现编次数上限（防止哪天群里疯狂刷题把 token 刷爆）。"""
    if int(max_per_day or 0) <= 0:
        return True
    today = time.strftime("%Y-%m-%d")
    d = {}
    try:
        with open(_QUOTA_FILE, encoding="utf-8") as f:
            d = json.load(f) or {}
    except Exception:
        d = {}
    if d.get("date") != today:
        d = {"date": today, "n": 0}
    if int(d.get("n") or 0) >= int(max_per_day):
        print("[quick_one] 今天已经现编过 %s 次了（上限 %s），先不编" % (d.get("n"), max_per_day))
        return False
    d["n"] = int(d.get("n") or 0) + 1
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = _QUOTA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, _QUOTA_FILE)
    except Exception:
        pass
    return True


def quick_one(cfg: dict, tries: int = 4, budget_max: int = 30000,
              max_per_day: int = 3, category=None) -> dict | None:
    """现编一道题（群里现货用完时用）。

    成功：返回带 id 的题目，并且**已经写进题库文件**（后面所有群都能抽到）；
    失败（模型不给格式/自评不过/花超预算）：返回 None，调用方自己决定兜底。
    """
    if not _quota_take(int(max_per_day or 0)):
        return None
    items = load_ai()
    briefs = all_brief()
    budget = {"tokens": 0}
    last = None
    for _ in range(max(1, int(tries or 1))):
        try:
            # 第一版自由发挥；之后都是「拿着上一版 + 退稿意见回炉改」
            r = _one(cfg, briefs, budget, repair=last, category=category)
        except Exception as e:
            print("[quick_one] 出错：%r" % (e,))
            return None
        if budget_max and budget["tokens"] > int(budget_max):
            print("[quick_one] 花到 %d token 了，先不编了" % budget["tokens"])
            return None
        if not r.get("ok"):
            why = r.get("why", "")
            print("[quick_one] 第 %d 次没过（%s）：%s" % (_ + 1, r.get("score", "-"), why[:80]))
            last = (r.get("puzzle") or (last or ({}, ""))[0], why)
            continue
        pz = dict(r["puzzle"])
        pz["id"] = _new_id(items)
        pz["created_at"] = int(time.time())
        pz["model"] = (_gen_cfg(cfg).get("memory", {}).get("provider") or {}).get("model")
        pz["on_demand"] = True
        if category:
            pz["category"] = str(category)
        items.append(pz)
        save_ai(items, {"tokens_last_run": budget["tokens"]})
        print("[quick_one] 现编一道 %s《%s》（%d token）" % (pz["id"], pz["title"], budget["tokens"]))
        return pz
    return None


def _new_id(items: list) -> str:
    n = 1
    ids = {str(x.get("id")) for x in items}
    while ("ai-%d" % n) in ids:
        n += 1
    return "ai-%d" % n


def classify_items(cfg: dict, items: list, batch: int = 8, force: bool = False) -> int:
    """批量打标（一次调用标 8 道）。force=True 时已分类的也重判。返回改了几道。"""
    todo = list(items) if force else [x for x in items if not x.get("category")]
    if not todo:
        return 0
    pcfg = _gen_cfg(cfg)
    label2cid = {v["label"]: k for k, v in curated.CATEGORIES.items()}
    menu = "、".join("%s(%s)" % (k, v["label"]) for k, v in curated.CATEGORIES.items())
    changed = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        lines = ["%d) 《%s》汤面：%s 汤底：%s" % (j, x.get("title"), x.get("surface"), x.get("bottom"))
                 for j, x in enumerate(chunk)]
        _desc = "；".join("%s=%s" % (k, v.get("desc", "")) for k, v in curated.CATEGORIES.items())
        sys_p = ("给下面每道海龟汤按「玩家玩完的主要感受」分类，只能选一个类别：%s。\n"
                 "判断要点：%s\n"
                 "狠一点：悲伤、意外死亡、生病、纯破案的都不算恐怖；拿不准就别塞进恐怖。\n"
                 "输出格式：JSON，形如 {'c': {'0': '类别id', '1': '类别id'}}，不要解释。" % (menu, _desc))
        try:
            txt, _u = llm.chat([{"role": "system", "content": sys_p},
                                {"role": "user", "content": "\n".join(lines)}],
                               pcfg, json_mode=True, max_tokens=400)
        except Exception as e:
            print("[classify] 出错：%r" % (e,))
            break
        d = _json_obj(txt)
        d = d.get("c") if isinstance(d.get("c"), dict) else d
        for j, x in enumerate(chunk):
            v = str(d.get(str(j)) or "")
            cid = v if v in curated.CATEGORIES else label2cid.get(v, "")
            if cid:
                old_cid = x.get("category")
                x["category"] = cid
                if cid != old_cid:
                    changed += 1
                print("[classify] %s《%s》%s → %s"
                      % (x.get("id"), x.get("title"),
                         ("(%s)" % old_cid) if old_cid else "", curated.CATEGORIES[cid]["label"]))
    return changed


def cmd_gen(cfg: dict, n: int, dry: bool, budget_max: int, category: str = "") -> int:
    items = load_ai()
    briefs = all_brief()
    budget = {"tokens": 0}
    made, tried, last = 0, 0, None
    while made < n and budget["tokens"] < budget_max:
        tried += 1
        try:
            r = _one(cfg, briefs, budget, repair=last, category=category or None)
        except Exception as e:
            print("[gen] 出错：%r" % (e,))
            break
        last = (r.get("puzzle") or (last or ({}, ""))[0], r.get("why", ""))
        tag = "✓" if r.get("ok") else "✗"
        print("[gen] %s 尝试%d %s | %s" % (tag, tried, r.get("why", ""), r["puzzle"].get("surface", "")[:40]))
        if not r.get("ok"):
            if tried >= n * 4:
                break
            continue
        pz = dict(r["puzzle"])
        pz["id"] = _new_id(items)
        pz["created_at"] = int(time.time())
        pz["model"] = (_gen_cfg(cfg).get("memory", {}).get("provider") or {}).get("model")
        if category:
            pz["category"] = str(category)
        items.append(pz)
        briefs.append("《%s》%s" % (pz["title"], pz["surface"]))
        made += 1
        print("    ✓ %s《%s》\n      汤面：%s\n      汤底：%s" % (pz["id"], pz["title"], pz["surface"], pz["bottom"]))
        if not dry:
            save_ai(items, {"tokens_last_run": budget["tokens"]})
    if not dry and made:
        save_ai(items, {"tokens_last_run": budget["tokens"]})
    print("[gen] 本次新增 %d 道，尝试 %d 次，用掉 %d token（题库共 %d 道：人工 %d + AI %d）"
          % (made, tried, budget["tokens"], len(curated.PUZZLES) + len(items),
             len(curated.PUZZLES), len(items)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="生成几道新题")
    ap.add_argument("--topup", action="store_true", help="补到 config.turtle.gen.target 道")
    ap.add_argument("--max-new", type=int, default=3, help="--topup 单次最多补几道（控费用）")
    ap.add_argument("--budget", type=int, default=60000, help="单次最多花多少 token")
    ap.add_argument("--dry", action="store_true", help="只打印不写文件")
    ap.add_argument("--list", action="store_true", help="列出现有 AI 题")
    ap.add_argument("--check", default="", help="复查某道 AI 题")
    ap.add_argument("--drop", default="", help="删掉某道 AI 题")
    ap.add_argument("--category", default="", help="指定类别：horror/mystery/warm/funny")
    ap.add_argument("--classify", action="store_true", help="给还没分类的 AI 题批量打标")
    ap.add_argument("--reclassify", action="store_true", help="全部重判一遍类别（修正分类不准）")
    a = ap.parse_args()
    cfg = _load_cfg()
    if a.list:
        for p in load_ai():
            print("%-6s 《%s》[%s]\n   汤面：%s\n   汤底：%s" % (p.get("id"), p.get("title"),
                                                             p.get("score"), p.get("surface"), p.get("bottom")))
        print("共 %d 道 AI 题（人工 %d 道）" % (len(load_ai()), len(curated.PUZZLES)))
        return 0
    if a.classify or a.reclassify:
        _items = load_ai()
        _n = classify_items(cfg, _items, force=bool(a.reclassify))
        if _n and not a.dry:
            save_ai(_items)
        print("分类完成：改了 %d 道" % _n)
        return 0
    if a.drop:
        items = [x for x in load_ai() if str(x.get("id")) != a.drop]
        save_ai(items)
        print("已删 %s，剩 %d 道 AI 题" % (a.drop, len(items)))
        return 0
    if a.check:
        items = load_ai()
        pz = next((x for x in items if str(x.get("id")) == a.check), None)
        if not pz:
            print("没有这道题：%s" % a.check)
            return 1
        budget = {"tokens": 0}
        r = _one(cfg, all_brief(), budget) if False else None
        crit_sys = CRIT_SYS % "\n".join("- " + b for b in all_brief()[-40:])
        txt, u = llm.chat([{"role": "system", "content": crit_sys},
                           {"role": "user", "content": "汤面：%s\n汤底：%s" % (pz.get("surface"), pz.get("bottom"))}],
                          _gen_cfg(cfg), json_mode=True, max_tokens=300)
        print("复查 %s《%s》：" % (a.check, pz.get("title")))
        print("  %s" % _json_obj(txt))
        print("  花掉 %s token" % ((u or {}).get("total_tokens")))
        return 0
    n = a.n
    if a.topup:
        g = ((cfg.get("turtle") or {}).get("gen") or {})
        target = int(g.get("target", 30) or 30)
        have = len(curated.PUZZLES) + len(load_ai())
        n = max(0, min(int(a.max_new), target - have))
        if n <= 0:
            print("[gen] 题库已经有 %d 道（目标 %d），这次不用补" % (have, target))
            return 0
    if n <= 0:
        ap.print_help()
        return 0
    return cmd_gen(cfg, n, a.dry, int(a.budget), a.category)


if __name__ == "__main__":
    sys.exit(main())
