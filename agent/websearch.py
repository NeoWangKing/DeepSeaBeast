# -*- coding: utf-8 -*-
"""上网搜索 + 读网页：给她"求证"用的眼睛。

设计：
- search()：优先用 AstrBot 里配好的搜索 provider（tavily/bocha/brave/firecrawl，key 在 WebUI 里配，
  我们直接复用它的实现，省得两处维护 key）；没配 key 就退回 Firecrawl 的免 key 搜索。
- read_url()：抓网页正文转纯文本（防 SSRF、防超大页、防乱码，带缓存），用来核对原文而不是只看标题。
- 缓存在 data/_web_cache：搜索结果 10 分钟、网页正文 1 小时 —— 省额度也更快。
- 查不到就明确说"没查到"，绝不返回能让她编下去的假内容。
"""
import gzip
import hashlib
import html as _html
import ipaddress
import json
import os
import re
import socket
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
CMD_CONFIG = "/opt/astrbot/data/cmd_config.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# AstrBot 里各家 key 的字段名
_KEY_FIELDS = {
    "tavily": "websearch_tavily_key",
    "bocha": "websearch_bocha_key",
    "brave": "websearch_brave_key",
    "exa": "websearch_exa_key",
    "firecrawl": "websearch_firecrawl_key",
    "baidu": "websearch_baidu_app_builder_key",
    "anysearch": "websearch_anysearch_key",
}
_PS_CACHE = {"t": 0.0, "ps": {}}


# ── 缓存 ──────────────────────────────────────────────────────────────
def _cache_dir() -> str:
    d = os.path.join(PLUGIN_DIR, "data", "_web_cache")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _cache_path(kind: str, key: str) -> str:
    h = hashlib.md5(str(key).encode("utf-8")).hexdigest()[:16]
    return os.path.join(_cache_dir(), "%s-%s.json" % (kind, h))


def _cache_get(kind: str, key: str, ttl: int):
    try:
        with open(_cache_path(kind, key), encoding="utf-8") as f:
            d = json.load(f)
        if time.time() - float(d.get("t") or 0) <= max(1, int(ttl or 1)):
            return d.get("v")
    except Exception:
        pass
    return None


def _cache_put(kind: str, key: str, val) -> None:
    try:
        with open(_cache_path(kind, key), "w", encoding="utf-8") as f:
            json.dump({"t": time.time(), "v": val}, f, ensure_ascii=False)
    except Exception:
        pass


# ── 搜索 provider ─────────────────────────────────────────────────────
def astrbot_provider_settings() -> dict:
    """AstrBot 自己的 provider_settings（key 在 WebUI 配，我们只读不写）。"""
    if time.time() - float(_PS_CACHE.get("t") or 0) < 60 and _PS_CACHE.get("ps"):
        return _PS_CACHE["ps"]
    ps = {}
    try:
        with open(CMD_CONFIG, encoding="utf-8-sig") as f:
            ps = (json.load(f) or {}).get("provider_settings") or {}
    except Exception:
        ps = {}
    _PS_CACHE["t"] = time.time()
    _PS_CACHE["ps"] = ps
    return ps


def _keys(ps: dict, name: str) -> list:
    v = ps.get(_KEY_FIELDS.get(name, "")) or []
    if isinstance(v, str):
        v = [v]
    return [str(x).strip() for x in v if str(x).strip()]


def provider_with_key(ps: dict) -> str:
    """挑一个「配了 key」的 provider（默认按 AstrBot 自己选的，再按优先级兜）。"""
    want = str(ps.get("websearch_provider") or "").strip().lower()
    if want and _keys(ps, want):
        return want
    for n in ("bocha", "tavily", "firecrawl", "exa"):
        if _keys(ps, n):
            return n
    return ""


def _astrbot_search(provider: str, ps: dict, query: str, count: int, freshness: str = "") -> list:
    """复用 AstrBot 自带的搜索实现（它自己管 key 轮换/重试）。"""
    import asyncio
    from astrbot.core.tools import web_search_tools as W
    fn = {"tavily": getattr(W, "_tavily_search", None),
          "bocha": getattr(W, "_bocha_search", None),
          "brave": getattr(W, "_brave_search", None),
          "firecrawl": getattr(W, "_firecrawl_search", None)}.get(provider)
    if fn is None:
        raise RuntimeError("AstrBot 里没有 %s 的实现" % provider)
    payload = {"query": query, "count": max(1, min(20, int(count or 5)))}
    if freshness:
        payload["freshness"] = freshness
    res = asyncio.run(fn(ps, payload))
    out = []
    for r in res or []:
        out.append({"title": str(getattr(r, "title", "") or ""),
                    "url": str(getattr(r, "url", "") or ""),
                    "snippet": str(getattr(r, "snippet", "") or "")})
    return out


def _firecrawl_search(query: str, count: int, key: str = "", timeout: int = 25) -> list:
    """Firecrawl 的搜索接口（没配 key 也能用，配了就更稳）。"""
    body = json.dumps({"query": query, "limit": max(1, min(10, int(count or 5)))}).encode("utf-8")
    h = {"Content-Type": "application/json", "User-Agent": UA}
    if key:
        h["Authorization"] = "Bearer " + key
    req = urllib.request.Request("https://api.firecrawl.dev/v1/search", data=body, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", "ignore") or "{}")
    data = d.get("data")
    web = (data.get("web") if isinstance(data, dict) else data) or []
    out = []
    for it in web:
        if not isinstance(it, dict):
            continue
        out.append({"title": str(it.get("title") or ""),
                    "url": str(it.get("url") or ""),
                    "snippet": str(it.get("description") or it.get("snippet") or "")})
    return out


def _firecrawl_scrape(url: str, key: str = "", timeout: int = 45) -> dict:
    """让 Firecrawl 代抓（我们直连被 Cloudflare 拦的站，比如 HLTV）。
    返回 {"markdown":…, "title":…}；失败返回 {}。"""
    try:
        body = json.dumps({"url": str(url), "formats": ["markdown"]}).encode("utf-8")
        h = {"Content-Type": "application/json", "User-Agent": UA}
        if key:
            h["Authorization"] = "Bearer " + str(key)
        req = urllib.request.Request("https://api.firecrawl.dev/v1/scrape", data=body,
                                     headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "ignore") or "{}")
        data = d.get("data") if isinstance(d.get("data"), dict) else {}
        md = str((data or {}).get("markdown") or "")
        meta = (data or {}).get("metadata") if isinstance((data or {}).get("metadata"), dict) else {}
        return {"markdown": md, "title": str((meta or {}).get("title") or "")} if md else {}
    except Exception:
        return {}


def md_to_text(md: str) -> str:
    """把 Firecrawl 抓回来的 markdown 洗成给模型看的纯文本（去图片/广告/多余空行）。"""
    s = str(md or "")
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)              # 图片
    s = re.sub(r"\[([^\]]{0,80})\]\([^)]*\)", r"\1", s)      # 链接保留文字
    keeps = []
    for line in s.splitlines():
        t = line.strip().strip("|").strip()
        if not t or re.fullmatch(r"[\-*_=·•\s]+", t):
            continue
        if len(t) <= 2:
            continue
        keeps.append(t)
    return re.sub(r"[ \t\u00a0]+", " ", "\n".join(keeps)).strip()


def search(query: str, count: int = 5, cfg: dict = None) -> dict:
    """联网搜索。返回 {"results":[{title,url,snippet}], "provider":str, "error":str}。"""
    cfg = cfg or {}
    q = str(query or "").strip()
    if not q:
        return {"results": [], "error": "查询是空的"}
    try:
        n = int(count or cfg.get("count") or 5)
    except Exception:
        n = 5
    n = max(1, min(10, n))
    ck = "%s|%d" % (q, n)
    cached = _cache_get("search", ck, int(cfg.get("cache_ttl", 600) or 600))
    if isinstance(cached, dict) and cached.get("results"):
        out = dict(cached)
        out["cached"] = True
        return out
    ps = astrbot_provider_settings()
    want = str(cfg.get("provider") or "auto").strip().lower()
    if want == "none":
        return {"results": [], "error": "搜索被配置关掉了"}
    order = []
    if want in ("", "auto"):
        p = provider_with_key(ps)
        if p:
            order.append(p)
        if "firecrawl" not in order:
            order.append("firecrawl")          # 免 key 兜底
    else:
        order = [want]
    err = ""
    for prov in order:
        try:
            key = (_keys(ps, prov) or [""])[0]
            if prov == "firecrawl":
                res = _firecrawl_search(q, n, key=key)
            else:
                res = _astrbot_search(prov, ps, q, n, str(cfg.get("freshness") or ""))
            res = [r for r in res if r.get("url")]
            if res:
                out = {"results": res[:n], "provider": prov}
                _cache_put("search", ck, out)
                return out
            err = "%s 没返回结果" % prov
        except Exception as e:
            err = "%s 失败：%s" % (prov, str(e)[:100])
    return {"results": [], "error": err or "没有可用的搜索通道"}


def search_text(query: str, count: int = 5, cfg: dict = None) -> str:
    """给模型看的一句话结果（紧凑、带网址，方便它挑一条去 read_url）。"""
    r = search(query, count, cfg)
    if not r.get("results"):
        return "搜不了：%s（那就直说你不确定，别编）" % (r.get("error") or "没结果")
    lines = ["搜到 %d 条（%s%s）：" % (len(r["results"]), r.get("provider") or "?",
                                      "，缓存" if r.get("cached") else "")]
    for i, it in enumerate(r["results"], 1):
        sn = re.sub(r"\s+", " ", str(it.get("snippet") or "")).strip()[:160]
        lines.append("%d. %s ｜ %s ｜ %s" % (i, str(it.get("title") or "").strip()[:70],
                                             str(it.get("url") or "").strip(), sn))
    lines.append("（要下结论就挑最关键的一条 read_url 看原文；别凭标题猜）")
    return "\n".join(lines)


# ── 判断"这是不是可查的事实问题"（用来提醒她先查、以及答"不知道"时兜底） ──
_LOOKUP_STRONG_RE = re.compile(
    r"(什么时候|啥时候|几号|哪天|多久|多少钱|谁赢|谁是?冠军|版本更新|新版本|下一?版|"
    r"上线时间|开服时间|活动时间|发售时间|复刻|卡池|打谁|谁打谁|对手是?谁|哪天打|几点打)", re.I)
_LOOKUP_WEAK_RE = re.compile(
    r"(更新|上线|开服|公测|定档|赛程|对阵|比分|冠军|排名|积分|价格|售价|发售|打折|"
    r"最新|新消息|进展|捷报|公告)", re.I)
_DONTKNOW_RE = re.compile(
    r"(不知道|不清楚|没听说|不了解|我哪知道|我又不是|不晓得|谁晓得|没有消息|没消息|"
    r"问官方|等官方|关注官方|查不了|搜不了|没找到|没查到|搜不到|搜了下|搜过了|网断了|网络不好|"
    r"连不上网|上不了网)", re.I)


def is_lookup_question(text) -> bool:
    """这句话是不是在问"可查的事实"（版本/时间/赛程/价格/谁是冠军…）。"""
    t = str(text or "").strip()
    if not t or len(t) > 140:
        return False
    if _LOOKUP_STRONG_RE.search(t):
        return True
    if _LOOKUP_WEAK_RE.search(t) and (("?" in t) or ("？" in t) or t.endswith(("吗", "呢", "吧"))):
        return True
    return False


_PROMISE_RE = re.compile(
    r"(我再(看|查|去|确认|找|问|想想)|再去(看|查|确认)|等下我|等我(看|查|确认)|"
    r"稍等|一会儿(再|回)|待会儿(再|回)|回头(再|告诉)|我看看|我找找|我去翻|马上回来|"
    r"让我确认|再确认一下|还需要确认)")


def is_dangling_promise(text) -> bool:
    """这句是不是"我再去看看/再查查"这种承诺（说完就该继续查，不能收工）。"""
    t = str(text or "").strip()
    if not t or len(t) > 80:
        return False
    return bool(_PROMISE_RE.search(t))


def is_dontknow(text) -> bool:
    """这句回复是不是"装傻式"的（不知道/我又不是内部人员…）——可查的问题上不能这么答。"""
    t = str(text or "").strip()
    if not t or len(t) > 80:
        return False
    return bool(_DONTKNOW_RE.search(t))


# ── 「要不要先查」的判定：规则优先，不像问句就不花钱，像问句让小模型判一次 ──
JUDGE_SYS = (
    "你是群聊助手的调度器。判断用户这句话要回答的话，是否必须查外部资料："
    "最新消息、具体事实、时间/日期/数据、别人最近说过什么、版本/赛程/价格。"
    "只输出一个字符：1=必须先查；0=不用查（闲聊、玩笑、情绪、称呼、观点、或助手本来就答得出的常识）。"
)
_QUESTION_HINT_RE = re.compile(
    r"[?？]|吗|呢|什么|啥|怎么|咋|为什么|为啥|如何|怎样|哪|哪些|哪几|哪家|谁|多少|几个|几时|几点|几号|多长|是不是|有没有|能不能|可不可以")
_JUDGE_CACHE = {}


_WEEK = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def now_text() -> str:
    """当前时间锚点（放提示词里，免得她分不清"下一场"和"上一场"）。"""
    try:
        t = time.localtime()
        return "%04d-%02d-%02d %02d:%02d %s" % (t.tm_year, t.tm_mon, t.tm_mday,
                                                t.tm_hour, t.tm_min, _WEEK[t.tm_wday])
    except Exception:
        return ""


def question_like(text) -> bool:
    """像不像在问事情（不像就别浪费一次判定调用）。"""
    t = str(text or "").strip()
    if not t or len(t) > 120:
        return False
    return bool(_QUESTION_HINT_RE.search(t))


def needs_lookup(text, chat_fn=None, recent: str = "") -> str:
    """返回 "yes" / "no" / ""（空=拿不准，交给她自己判断）。

    - 规则强命中（什么时候/几号/谁赢/多少钱…）→ 直接 yes，不花钱
    - 不像问句 → ""，不花钱
    - 其余问句 → 让 chat_fn(messages)->str 判一次（结果缓存 30 分钟）
    """
    t = str(text or "").strip()
    if not t:
        return ""
    if is_lookup_question(t):
        return "yes"
    if not question_like(t) or chat_fn is None:
        return ""
    key = re.sub(r"\s+", " ", t)[:120]
    hit = _JUDGE_CACHE.get(key)
    if hit and time.time() - float(hit[0]) <= 1800:
        return hit[1]
    try:
        msgs = [{"role": "system", "content": JUDGE_SYS}]
        if recent:
            msgs.append({"role": "user", "content": "（最近群聊，仅供参考）\n" + str(recent)[:300]})
        msgs.append({"role": "user", "content": t})
        out = str(chat_fn(msgs) or "").strip()
        d = ("yes" if out[:1] in ("1", "是", "y", "Y")
             else ("no" if out[:1] in ("0", "不", "n", "N") else ""))
    except Exception:
        d = ""
    _JUDGE_CACHE[key] = (time.time(), d)
    if len(_JUDGE_CACHE) > 300:
        _JUDGE_CACHE.clear()
    return d


def should_force(decision: str, evidence: float = 0.0, threshold: float = 0.34) -> bool:
    """要不要强制先查：判定 yes，且本地资料库/记忆里没有足够证据（先回忆再查）。"""
    if str(decision or "") != "yes":
        return False
    try:
        return float(evidence or 0.0) < float(threshold or 0.34)
    except Exception:
        return True


def remember_note(question, answer, max_len: int = 120) -> dict:
    """查证过的一问一答 → 写进她记忆的条目（下次同类问题直接想起来）。"""
    q = re.sub(r"\s+", " ", str(question or "")).strip()
    a = re.sub(r"\s+", " ", str(answer or "")).strip()
    if not q or not a or len(q) > 60 or len(a) > max_len:
        return {}
    return {"kind": "topic", "text": "他问过「%s」，我查证后回：%s" % (q[:60], a[:max_len])}


# ── 读网页 ────────────────────────────────────────────────────────────
def _decode(raw: bytes, charset: str = "") -> str:
    for enc in [charset, "utf-8", "gb18030", "big5", "latin-1"]:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "ignore")


def _safe_url(url: str) -> tuple:
    """SSRF 防护：只允许 http/https 的公网地址（禁内网/本机/保留地址）。"""
    try:
        u = urllib.parse.urlparse(str(url or "").strip())
    except Exception:
        return False, "网址看不懂"
    if u.scheme not in ("http", "https"):
        return False, "只支持 http/https"
    host = (u.hostname or "").strip()
    if not host:
        return False, "没有域名"
    h = host.lower()
    if h == "localhost" or h.endswith(".local") or h.endswith(".internal"):
        return False, "内网地址不给读"
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False, "域名解析失败"
    for info in infos:
        try:
            ip = info[4][0]
            a = ipaddress.ip_address(ip.split("%")[0])
        except Exception:
            continue
        if (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved
                or a.is_multicast or a.is_unspecified):
            return False, "内网/保留地址不给读"
    return True, ""


def fetch_page(url: str, timeout: int = 20, max_bytes: int = 1_500_000) -> tuple:
    ok, why = _safe_url(url)
    if not ok:
        raise RuntimeError(why)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate", "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(max_bytes)
        enc = (r.headers.get("Content-Encoding") or "").lower()
        ctype = str(r.headers.get("Content-Type") or "")
        charset = ""
        try:
            charset = r.headers.get_content_charset() or ""
        except Exception:
            charset = ""
        final = r.geturl()
    if "gzip" in enc:
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    return final, ctype, _decode(raw, charset)


def html_to_text(doc: str) -> tuple:
    """粗糙但够用的正文提取：返回 (标题, 正文)。"""
    s = str(doc or "")
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", s)
    title = re.sub(r"\s+", " ", _html.unescape(re.sub(r"(?s)<[^>]+>", " ", m.group(1)))).strip() if m else ""
    if not title:
        m2 = re.search(r'(?is)<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', s)
        title = _html.unescape(m2.group(1)).strip() if m2 else ""
    s = re.sub(r"(?is)<(script|style|noscript|svg|template|iframe|nav|footer|header|aside|form)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    s = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6]|/tr)[^>]*>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t\u00a0\u3000]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return title, s.strip()


def read_text(url: str, cfg: dict = None) -> str:
    """读一个网页的正文，给模型看。"""
    cfg = cfg or {}
    u = str(url or "").strip()
    if not u:
        return "没读：url 是空的"
    if u.startswith("//"):
        u = "https:" + u
    elif not u.lower().startswith("http"):
        u = "https://" + u
    ttl = int(cfg.get("read_cache_ttl", 3600) or 3600)
    cap = int(cfg.get("read_max_chars", 4000) or 4000)
    cached = _cache_get("page", u, ttl)
    if isinstance(cached, str) and cached:
        return "（缓存）" + cached
    final, title, body, _err = u, "", "", ""
    try:
        final, ctype, raw = fetch_page(u, timeout=int(cfg.get("timeout", 20) or 20))
        if "json" in ctype.lower():
            body, title = raw.strip()[:cap], "网页数据"
        else:
            title, body = html_to_text(raw)
            body = body[:cap]
    except Exception as e:
        _err = str(e)[:120]
    # 直连被拦（403/Cloudflare）或没抓到正文 → 让 Firecrawl 代抓（HLTV 这类站只能这么读）
    if len(body) < 40:
        try:
            _ps = astrbot_provider_settings()
            _fc = _firecrawl_scrape(u, key=(_keys(_ps, "firecrawl") or [""])[0],
                                    timeout=int(cfg.get("scrape_timeout", 45) or 45))
            _b2 = md_to_text(_fc.get("markdown") or "")
            if len(_b2) > len(body or ""):
                title = _fc.get("title") or title
                body = _b2[:cap]
                final = u
                _err = ""
        except Exception as e2:
            _err = _err or str(e2)[:80]
    if len(body) < 40:
        _why = _err or "可能要登录/靠 JS 渲染"
        return "这个网页没抓到正文（%s），换一条读或直接说没查到" % _why
    tail = "…（太长，只读了前 %d 字）" % cap if len(body) >= cap else ""
    out = "【%s】%s\n%s%s" % (title[:70] or "网页", final, body, tail)
    _cache_put("page", u, out)
    return out
