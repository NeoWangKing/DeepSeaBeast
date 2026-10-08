"""离线验证：上网搜索 + 读网页（不联网，全部打桩）。

跑法：python3 tests/websearch_sim.py
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from agent import tools as T          # noqa: E402
from agent import websearch as W      # noqa: E402
from agent import loop as L           # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


print("== 网页正文提取 ==")
html = ("<html><head><title>标题 &amp; 测试</title><style>a{}</style><script>var x=1</script></head>"
        "<body><nav>导航栏</nav><h1>大标题</h1><p>第一段正文。</p><div>第二段<br>换行</div>"
        "<footer>页脚</footer></body></html>")
title, body = W.html_to_text(html)
ck("标题正确（含转义）", title == "标题 & 测试", repr(title))
ck("脚本/样式被剥掉", "var x=1" not in body and "a{}" not in body)
ck("导航/页脚被剥掉", "导航栏" not in body and "页脚" not in body)
ck("正文保留", "第一段正文。" in body and "换行" in body)

print("== SSRF 防护 ==")
for u, want in (("http://127.0.0.1/x", False), ("http://192.168.1.5:8080/", False),
                ("http://169.254.169.254/latest/meta-data/", False), ("http://10.0.0.1/", False),
                ("http://172.16.0.1/", False), ("file:///etc/passwd", False),
                ("ftp://example.com/a", False), ("http://8.8.8.8/", True)):
    ok, why = W._safe_url(u)
    ck("安全判定 %s" % u[:34], ok == want, why)

print("== 缓存 ==")
W._cache_put("search", "unit|1", {"results": [{"url": "http://x"}], "provider": "t"})
ck("写入后能读到", (W._cache_get("search", "unit|1", 600) or {}).get("provider") == "t")
_p = W._cache_path("search", "unit|1")
_d = json.load(open(_p, encoding="utf-8"))
_d["t"] = time.time() - 100
json.dump(_d, open(_p, "w", encoding="utf-8"))
ck("过期（100 秒前写的）读不到", W._cache_get("search", "unit|1", 60) is None)

print("== 二游官网清单（资料库） ==")
_kbp = os.path.join(ROOT, "data", "kb", "games-official-sites.md")
if os.path.isfile(_kbp):
    _kt = open(_kbp, encoding="utf-8").read()
    for _u in ("ak.hypergryph.com", "endfield.hypergryph.com", "yuanshen.com", "sr.mihoyo.com",
               "zzz.mihoyo.com", "mc.kurogames.com"):
        ck("清单含 %s" % _u, _u in _kt)
    ck("清单写了「先看官网」", "先看对应官网的公告" in _kt)
else:
    print("  （资料库文件不存在，跳过——KB 内容不入库，属正常）")
_sec_sites = open(os.path.join(ROOT, "promptlib", "sections.py"), encoding="utf-8").read()
ck("提示词要求游戏信息先查官网", "先查对应官网" in _sec_sites)

print("== 代抓回退（HLTV 这类防爬站） ==")
_md = ("![](https://x/a.png) [**Spirit**](https://www.hltv.org/team/7020) vs Falcons\n\n"
       "| Oct 9 | 18:00 | ESL Pro League |\n" + "\n" * 3 + "· · ·")
_txt = W.md_to_text(_md)
ck("图片/链接语法被清掉", "![" not in _txt and "](http" not in _txt)
ck("保留正文与队伍名", "Spirit" in _txt and "Falcons" in _txt)
ck("多余空行被压缩", "\n\n\n" not in _txt)
_src_ws = open(os.path.join(ROOT, "agent", "websearch.py"), encoding="utf-8").read()
ck("直连失败会走 Firecrawl 代抓", "_firecrawl_scrape" in _src_ws and "md_to_text(_fc" in _src_ws)
_kb = os.path.join(ROOT, "data", "kb", "cs2-esports.md")
if os.path.isfile(_kb):
    _ks = open(_kb, encoding="utf-8").read()
    ck("KB 里写了 HLTV 查询渠道", "hltv.org" in _ks and "查询渠道" in _ks)
_sk = open(os.path.join(ROOT, "skills", "verify", "SKILL.md"), encoding="utf-8").read()
ck("求证技能里写了来源优先级", "HLTV" in _sk and "Liquipedia" in _sk)

print("== 结果格式化（打桩搜索） ==")
W.search = lambda q, n=5, c=None: {"results": [{"title": "T1", "url": "http://a",
                                                "snippet": "摘 要" * 3}], "provider": "fake"}
txt = W.search_text("问题", 3, {})
ck("带条数/来源", "搜到 1 条（fake）" in txt)
ck("带网址和摘要", "http://a" in txt and "摘 要" in txt)
ck("提醒看原文", "别凭标题猜" in txt)
W.search = lambda q, n=5, c=None: {"results": [], "error": "没有可用通道"}
ck("查不到时明确说没查到", W.search_text("问题").startswith("搜不了"))

print("== 工具层 ==")
got = []
cb = {"web_search": lambda q, n=5: (got.append(("s", q, n)) or "搜到 2 条（fake）：\n1. x"),
      "read_url": lambda u: (got.append(("r", u)) or "【标题】http://x\n正文")}
t = T.Tools("869622030", cb, {"max_calls": 5})
ck("web_search 正常返回", "搜到 2 条" in t.web_search("IEM 冠军", 3))
ck("参数透传（query/count）", got[-1] == ("s", "IEM 冠军", 3), str(got[-1]))
ck("query 空 → 不查", t.web_search("").startswith("没查"))
ck("read_url 正常返回", "正文" in t.read_url("http://x"))
ck("url 空 → 不读", t.read_url("").startswith("没读"))
ck("记进 searched（审计）", t.searched == ["IEM 冠军"])
ck("回调缺失时不炸", T.Tools("g", {}, {}).web_search("q").startswith("没查"))

print("== schema / required ==")
on = L.spec_to_openai([s for s in T.spec_list({"search": True}) if s[0] in ("web_search", "read_url")])
ck("search 开着时有两个工具", len(on) == 2, str([x["function"]["name"] for x in on]))
ck("web_search.query 必填",
   [x for x in on if x["function"]["name"] == "web_search"][0]["function"]["parameters"]["required"] == ["query"])
ck("read_url.url 必填",
   [x for x in on if x["function"]["name"] == "read_url"][0]["function"]["parameters"]["required"] == ["url"])
names = [s[0] for s in T.spec_list({"search": False})]
ck("search 关着时不注册", "web_search" not in names and "read_url" not in names)

print()
if FAIL:
    print("有问题：%s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
