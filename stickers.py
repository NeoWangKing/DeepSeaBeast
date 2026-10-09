"""表情包收藏夹：自动收图 → 识图打标 → 合适的时候挑一张发。

- 存储：data/stickers/<id>.<ext> + index.json
- 去重：8x8 均值哈希（同一张图只留一份）
- 打标：GLM-4V-Flash（先判断"是不是适合转发的梗图/表情包"；自拍/截图/二维码直接拒收）
- 选图：按标签/关键词匹配，最近用过的排除
"""
import base64
import hashlib
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "stickers")
INDEX = os.path.join(DATA, "index.json")

# 「她自己的形象」这组表情：pick 时加分、表情清单里常驻。
# 配置：stickers.prefer_ids（id 列表）、stickers.prefer_boost（默认加几分）。
_PREFER_CACHE = {"key": None, "ids": [], "boost": 3.0}


def prefer_cfg() -> dict:
    """读 stickers.prefer_ids / prefer_boost（按文件 mtime 缓存，改配置免重启）。"""
    p = os.path.join(HERE, "config.json")
    try:
        key = os.path.getmtime(p)
    except Exception:
        key = None
    if _PREFER_CACHE["key"] == key and _PREFER_CACHE["key"] is not None:
        return {"ids": _PREFER_CACHE["ids"], "boost": _PREFER_CACHE["boost"]}
    ids, boost = [], 3.0
    try:
        c = json.load(open(p, encoding="utf-8")).get("stickers") or {}
        ids = [str(x) for x in (c.get("prefer_ids") or []) if str(x).strip()]
        boost = float(c.get("prefer_boost", 3) or 0)
    except Exception:
        pass
    _PREFER_CACHE.update({"key": key, "ids": ids, "boost": boost})
    return {"ids": ids, "boost": boost}

VISION_RULES = """这张图是不是"适合收进 QQ 表情收藏夹、拿来当表情包发"的图？

**主人的规矩：只要表情包，不要图片；表情尽量卡通风格、有趣好玩。**

先判 kind（这张图到底是什么）：
- meme=表情包/梗图（包括"截图被加工成梗"：加了吐槽文字、涂掉隐私信息当段子）
- cartoon=卡通/动漫/Q版/手绘/emoji 风格的图（没文字也算）
- animal=可爱动物的表情图（宠物卖萌图、被加工过的萌宠表情）
- photo=实拍照片（真人、风景、美食、物品、商品实拍、桌面、随手拍）
- group_photo=合影/集体照/战队照/选手照/颁奖照
- screenshot=屏幕截图（游戏画面、战绩/数据界面、软件或网页界面、聊天记录、系统提示、纯文字长图）
- poster=海报/宣传图/活动公告/菜单/价格表/名片
- qr=二维码；ad=广告；other=说不清

再判 style：cartoon=卡通/动漫/emoji｜real=真人或实拍｜text=纯文字｜mixed=混着来

**硬规则（命中就直接 worth=false）**：
- kind 是 screenshot / poster / photo / group_photo / qr / ad → 不收
- 主体是真人或实拍 → 不收（除非整张已被重度加工成梗：加了明显吐槽文字 + 夸张表情，
  这种 kind=meme、style=real、fun 打到 8 以上才可能收）
- 风景、美食、宠物日常照、商品实拍、纯文字长图 → 不收

**有趣好玩的打分 fun（0~10）**：
- 8~10：看见就想笑 / 很想拿来接话（夸张表情、沙雕、有梗文字、萌到爆）
- 5~7：还行，能用但没特别
- 0~4：平淡、普通、不好笑

还有 cartoon：这张图是不是卡通/动漫/Q版/emoji 画风（true/false）。
判的时候**从严**，拿不准就把 fun 打低、worth 判 false——收错了要主人手动去删，比漏收一张麻烦得多。

只输出 JSON：{"kind": "上面那几种之一", "style": "cartoon|real|text|mixed",
"is_meme": true/false（算不算表情包/梗图）, "worth": true/false（值不值得收进收藏夹）,
"fun": 0~10 的整数, "cartoon": true/false, "desc": "≤14字描述", "tags": ["3~5个中文标签"]}
"""


def _vision_key() -> str:
    for p in (os.path.join(HERE, ".secrets", "glm.key"), "/opt/astrbot/data/plugins/qq_peak_gate/.secrets/glm.key"):
        try:
            k = open(p, encoding="utf-8").read().strip()
            if k:
                return k
        except Exception:
            pass
    return ""


def tag_image(path: str, model: str = "glm-4v-flash", key: str = "") -> dict:
    """识图打标：返回 {is_meme, desc, tags}；失败返回 {}。"""
    key = key or _vision_key()
    if not key:
        return {}
    try:
        b64 = base64.b64encode(open(path, "rb").read()).decode()
        ext = os.path.splitext(path)[1].lstrip(".").lower() or "png"
        if ext in ("gif", "webp", "bmp"):      # 识图接口不收动图：取第一帧转 jpg 再送
            try:
                import io
                from PIL import Image as _PIL
                _im = _PIL.open(path)
                try:
                    _im.seek(0)
                except Exception:
                    pass
                _buf = io.BytesIO()
                _im.convert("RGB").save(_buf, format="JPEG", quality=85)
                b64 = base64.b64encode(_buf.getvalue()).decode()
                ext = "jpeg"
            except Exception:
                pass
        body = {"model": model, "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/%s;base64,%s" % (ext, b64)}},
            {"type": "text", "text": VISION_RULES}]}], "temperature": 0.1}
        req = urllib.request.Request(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        d = json.load(urllib.request.urlopen(req, timeout=90))
        txt = ((d.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "")
        import re
        m = re.search(r"\{.*\}", str(txt), re.S)
        out = json.loads(m.group(0)) if m else {}
        _w = out.get("worth")
        if _w is None:
            for _k in ("collect", "fun", "interesting"):
                if out.get(_k) is not None:
                    _w = out.get(_k)
                    break
        try:
            _fun = int(out.get("fun")) if out.get("fun") is not None else None
        except Exception:
            _fun = None
        return {"is_meme": bool(out.get("is_meme")),
                "worth": (None if _w is None else bool(_w)),
                "fun": _fun,
                "style": str(out.get("style") or "")[:12].lower(),
                "cartoon": (None if out.get("cartoon") is None else bool(out.get("cartoon"))),
                "kind": str(out.get("kind") or "")[:16],
                "desc": str(out.get("desc") or "")[:30],
                "tags": [str(x)[:8] for x in (out.get("tags") or []) if str(x).strip()][:6],
                "token": int((d.get("usage") or {}).get("total_tokens") or 0)}
    except Exception as e:
        print("[stickers] 识图失败: %r" % (e,))
        return {}


def image_size(path: str) -> int:
    """图片长边像素（拿不到返回 0）。"""
    try:
        from PIL import Image
        w, h = Image.open(path).size
        return int(max(w, h))
    except Exception:
        return 0


def judge_collect(g: dict, private: bool = False, sub_type=None, long_side: int = 0,
                  explicit: bool = False) -> tuple:
    """这张图收不收进收藏夹，返回 (keep, reason)。

    主人的规矩（2026-10-08）：**只收表情，不收图片**；表情尽量卡通、有趣好玩。
    - sub_type==1 才是 QQ 面板表情（图片元素里的 sub_type；AstrBot 的 Image 组件会丢，
      得从原始报文的 raw_message 里取）。普通图片（sub_type==0）默认不收。
    - explicit=True：主人明确说了"收藏/存起来"，这时才允许收普通图片（仍要看识图结论）。
    """
    g = g or {}
    kind = str(g.get("kind") or "").lower()
    style = str(g.get("style") or "").lower()
    try:
        fun = int(g.get("fun") or 0)
    except Exception:
        fun = 0
    # 1) 只收表情
    if not explicit and sub_type != 1:
        if sub_type is None:
            return False, "拿不到图片类型，按普通图片处理（只收表情）"
        return False, "不是 QQ 表情（sub_type=%s），是普通图片" % (sub_type,)
    if not g:
        return (True, "识图失败，但它确实是表情元素，先收下") if sub_type == 1 \
            else (False, "识图失败")
    # 2) 拍照/截图/海报类一律不收
    if kind in ("screenshot", "poster", "photo", "group_photo", "qr", "ad"):
        return False, "识图判成 %s（截图/实拍/海报类不收）" % (kind or "?")
    if style == "text":
        return False, "纯文字图，不是表情"
    if g.get("is_meme") is False:
        return False, "识图觉得不算表情包"
    if g.get("worth") is False:
        return False, "识图觉得没意思"
    if long_side and int(long_side) > 1600:
        return False, "%dpx 太大，长的不像表情" % int(long_side)
    # 3) 尽量卡通：实拍/真人要明显好玩才收
    real = (style == "real") or (g.get("cartoon") is False)
    if real and fun < 8:
        return False, "实拍/真人风格，不够好玩（fun=%d）" % fun
    if fun and fun < 4:
        return False, "不太好玩（fun=%d）" % fun
    return True, "表情：%s/style=%s/fun=%d" % (kind or "?", style or "?", fun)


# ---------------- SnowLuma 面板接口（读/写那个 QQ 号自己的表情收藏） ----------------
PANEL = "http://127.0.0.1:5099"
PANEL_PASS_CANDIDATES = [
    os.path.join(HERE, ".secrets", "panel.pass"),
    "/root/.qqbot-panel-pass",
]
PANEL_PASS = PANEL_PASS_CANDIDATES[0]
SELF_UIN = "3237702352"


def _panel_token() -> str:
    """登录 SnowLuma 控制台拿 token（面板在 127.0.0.1，仅本机）。"""
    try:
        pw = ""
        for _p in PANEL_PASS_CANDIDATES:
            try:
                pw = open(_p, encoding="utf-8").read().strip()
                if pw:
                    break
            except Exception:
                continue
        if not pw:
            raise RuntimeError("panel pass unreadable")
        body = json.dumps({"username": "admin", "password": pw}).encode()
        req = urllib.request.Request(PANEL + "/api/login", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=15).read().decode()).get("token") or ""
    except Exception as e:
        print("[stickers] 面板登录失败: %r" % (e,))
        return ""


def panel(action: str, params: dict = None, uin: str = "") -> object:
    """调 SnowLuma 的动作（debug/invoke）。返回 data 或 None。"""
    tok = _panel_token()
    if not tok:
        return None
    body = {"uin": uin or SELF_UIN, "action": action, "params": params or {}}
    try:
        req = urllib.request.Request(PANEL + "/api/debug/invoke", data=json.dumps(body).encode(),
                                     method="POST",
                                     headers={"Authorization": "Bearer " + tok,
                                              "Content-Type": "application/json"})
        d = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
        if d.get("status") == "ok":
            return d.get("data")
        print("[stickers] %s 失败: %s" % (action, d.get("message")))
    except Exception as e:
        print("[stickers] %s 出错: %r" % (action, e))
    return None


def fetch_faces(uin: str = "", count: int = 100) -> list:
    """账号表情收藏里的图（返回 URL 列表）。"""
    d = panel("fetch_custom_face", {"count": int(count), "return_type": "url"}, uin)
    if isinstance(d, list):
        return [str(x) for x in d if str(x).startswith("http")]
    if isinstance(d, dict):
        return [str(v) for v in d.values() if str(v).startswith("http")]
    return []


def add_face(src: str, uin: str = "") -> bool:
    """把图片加进那个 QQ 号的收藏面板（失败重试 2 次：面板偶发超时/限频）。"""
    for _i in range(2):
        if panel("add_custom_face", {"file": src}, uin):
            return True
        time.sleep(1.5)
    return False


def download_by_file_id(file_id: str) -> str:
    """用客户端会话下载历史图片（返回本地临时文件路径）。"""
    d = panel("download_file_image_stream", {"file_id": str(file_id)})
    for k in ("path", "file", "local", "url"):
        v = ""
        if isinstance(d, dict):
            v = str(d.get(k) or "")
        if v and os.path.exists(v):
            return v
    return ""


def _ext_of(data: bytes) -> str:
    """按真实文件头判断扩展名（表情很多是 GIF，存成 .jpg 会导致上传类型不符）。"""
    if not data:
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] in (b"BM",):
        return ".bmp"
    return ".jpg"


def _download_remote(url: str, timeout: int = 25) -> str:
    import tempfile
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=timeout).read(3 * 1024 * 1024)
        if not data:
            return ""
        fd, path = tempfile.mkstemp(suffix=_ext_of(data), prefix="stk_")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return path
    except Exception as e:
        print("[stickers] 下载失败 %s: %r" % (url[:60], e))
        return ""


def add_remote(url: str, group: str = "", desc: str = "", tags=None, max_store: int = 300,
               face: bool = False) -> dict | None:
    """把一张"网图/账号表情"登记进索引（不下载正文，直接用 URL 发）。返回条目。"""
    items = load()
    h = hashlib.md5(str(url).encode()).hexdigest()[:16]
    if any(str(x.get("hash")) == h for x in items):
        return None
    sid = "s%d" % int(time.time() * 1000)
    it = {"id": sid, "file": "", "url": str(url), "hash": h, "desc": desc or "",
          "tags": list(tags or []), "group": str(group or ""), "added_at": int(time.time()),
          "used": 0, "last_used": 0, "size": 0, "src": "account" if face else "web"}
    items.append(it)
    if len(items) > int(max_store or 300):
        items.sort(key=lambda x: (int(x.get("used") or 0), int(x.get("last_used") or x.get("added_at") or 0)))
        items = items[len(items) - int(max_store or 300):]
    save(items)
    return it


def sync_account(uin: str = "", limit: int = 200, max_store: int = 300, group: str = "") -> int:
    """把那个 QQ 号表情收藏里的表情同步进索引（识图打标）。返回新增数量。"""
    urls = fetch_faces(uin, limit)
    print("[stickers] 账号表情 %d 张" % len(urls))
    n = 0
    for u in urls:
        path = _download_remote(u, 20)
        if not path:
            continue
        try:
            g = tag_image(path)
            if g.get("is_meme") or True:      # 账号里已有的直接收，识图只用来打标
                it = add_remote(u, group=group, desc=g.get("desc", ""), tags=g.get("tags") or [],
                                max_store=max_store, face=True)
                if it:
                    n += 1
                    print("  + %s《%s》%s" % (it["id"], it.get("desc"), ",".join(it.get("tags") or [])))
        finally:
            try:
                os.remove(path)
            except Exception:
                pass
    return n


def import_history(group: str, limit: int = 40, max_store: int = 300, add_to_face: bool = True) -> int:
    """从 SnowLuma 的消息库里翻出某个群最近的图片，识图合格的就收进表情库（可选同时加进账号表情）。"""
    import re as _re
    import shutil
    import sqlite3
    src_dir = "/opt/snowluma/data/3237702352"
    tmp = "/tmp/stk_db"
    os.makedirs(tmp, exist_ok=True)
    for suf in ("", "-wal", "-shm"):
        try:
            shutil.copyfile(src_dir + "/messages.db" + suf, tmp + "/messages.db" + suf)
        except Exception:
            pass
    try:
        c = sqlite3.connect(tmp + "/messages.db")
        rows = list(c.execute(
            "select data from messages where session_id like ? order by timestamp desc limit 400",
            ("%" + str(group) + "%",)))
    except Exception as e:
        print("[stickers] 读消息库失败: %r" % (e,))
        return 0
    ids, seen = [], set()
    for (d,) in rows:
        for fid in _re.findall(r"fileid=([A-Za-z0-9_\-]{20,})", str(d)):
            fid = fid.replace("&amp;", "")
            if fid not in seen:
                seen.add(fid)
                ids.append(fid)
        for fid in _re.findall(r"([0-9A-F]{32}\.(?:jpg|png|gif))", str(d)):
            if fid not in seen:
                seen.add(fid)
                ids.append(fid)
    print("[stickers] 找到 %d 个候选图片" % len(ids))
    n = 0
    for fid in ids[: int(limit or 40)]:
        path = download_by_file_id(fid) or ""
        if not path:
            continue
        try:
            g = tag_image(path)
            if not g.get("is_meme"):
                continue
            if add_to_face:
                add_face(path, "")
            it = add_file(path, group=group, desc=g.get("desc", ""), tags=g.get("tags") or [],
                          max_store=max_store, ge=g)
            if it:
                n += 1
                print("  + %s《%s》%s" % (it["id"], it.get("desc"), ",".join(it.get("tags") or [])))
        finally:
            try:
                os.remove(path)
            except Exception:
                pass
    return n


def ensure_local(it: dict) -> str:
    """要发账号表情时，先把图缓存到本地（QQ 对 qq_expression 直链常常发不出去）。返回本地路径。"""
    p = abs_path(it)
    if p and os.path.isfile(p) and os.path.getsize(p) > 0:
        return p
    url = str(it.get("url") or "")
    if not url:
        return ""
    tmp = _download_remote(url, 25)
    if not tmp:
        return ""
    fn = str(it.get("id") or os.path.basename(tmp)) + os.path.splitext(tmp)[1]
    dst = os.path.join(DATA, fn)
    try:
        import shutil
        os.makedirs(DATA, exist_ok=True)
        shutil.copyfile(tmp, dst)
        os.remove(tmp)
    except Exception:
        return tmp
    items = load()
    for x in items:
        if str(x.get("id")) == str(it.get("id")):
            x["file"] = fn
            x["size"] = os.path.getsize(dst)
            break
    save(items)
    return dst


def push_to_face(limit: int = 50) -> int:
    """把库里（本地收藏的）图推到那个 QQ 号自己的表情收藏里。返回推了几张。"""
    items = load()
    n = 0
    for it in items[: int(limit or 50)]:
        if it.get("src") == "account" and it.get("face_pushed"):
            continue
        p = ensure_local(it)
        if not p or not os.path.isfile(p):
            continue
        if add_face(p):
            n += 1
            it["face_pushed"] = True
            print("  ↑ 已加进 QQ 表情:", it.get("id"), it.get("desc") or "")
    if n:
        save(items)
    return n


def push_pending(limit: int = 80) -> int:
    """只补推"还没进面板"的那些（面板偶发失败自愈；不碰已推过的、也不回推账号同步来的）。"""
    items = load()
    n = 0
    for it in items[: int(limit or 80)]:
        if it.get("face_pushed") or it.get("src") == "account":
            continue
        p = ensure_local(it)
        if not p or not os.path.isfile(p):
            continue
        if add_face(p):
            n += 1
            it["face_pushed"] = True
            print("  ↑ 补推进面板:", it.get("id"), it.get("desc") or "")
    if n:
        save(items)
    return n


def load() -> list:
    try:
        with open(INDEX, encoding="utf-8") as f:
            return list((json.load(f) or {}).get("items") or [])
    except Exception:
        return []


def save(items: list) -> None:
    os.makedirs(DATA, exist_ok=True)
    tmp = INDEX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"updated_at": int(time.time()), "items": items}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, INDEX)


def ahash(path: str) -> str:
    """8x8 均值哈希（感知去重）。拿不到图片库就退化成 md5。"""
    try:
        from PIL import Image
        im = Image.open(path).convert("L").resize((8, 8))
        px = list(im.getdata())
        avg = sum(px) / float(len(px))
        bits = "".join("1" if p > avg else "0" for p in px)
        return "%016x" % int(bits, 2)
    except Exception:
        try:
            return hashlib.md5(open(path, "rb").read()).hexdigest()[:16]
        except Exception:
            return ""


def abs_path(item: dict) -> str:
    fn = str((item or {}).get("file") or "").strip()
    return os.path.join(DATA, fn) if fn else ""


# 情绪/场景词 → 相关标签（她说"发个无语的"，库里写的是"尴尬/嫌弃/离谱"，靠这个对上）
_MOOD_SYN = {
    "无语": "无语 尴尬 离谱 嫌弃 服了 汗",
    "笑死": "笑死 搞笑 梗图 滑稽 哈哈",
    "点赞": "点赞 赞同 同意 认可 牛",
    "晚安": "晚安 睡 困 拜拜 晚安啦",
    "开心": "开心 高兴 卖萌 可爱 跳舞 好耶",
    "生气": "生气 嫌弃 傲娇 不满 烦",
    "摆烂": "摆烂 躺平 佛系 累 不想努力",
    "问号": "疑问 问号 懵 不懂 啥",
    "加油": "加油 鼓励 打气 冲",
    "哭": "哭 委屈 可怜 惨 泪",
    "猫": "猫猫 猫 喵",
    "狗": "狗 狗头 汪汪",
    "敷衍": "敷衍 附和 尴尬 客套",
    "震惊": "震惊 离谱 惊了 瞪",
}


def pick(text: str, tags_hint: list = None, exclude_ids=(), limit: int = 6,
         prefer=None, prefer_boost=None) -> list:
    """按文本里出现的关键词/标签挑候选（返回若干条，交给模型再选）。

    prefer：优先露面的 id（默认读 stickers.prefer_ids，即「她自己的形象」那组），
    命中这些 id 的候选加 prefer_boost 分，但最近用过的扣分照旧（换着发、别刷屏）。
    """
    items = load()
    ex = set(str(x) for x in (exclude_ids or []))
    _pf = prefer_cfg()
    pset = set(_pf["ids"] if prefer is None else [str(x) for x in prefer])
    pboost = _pf["boost"] if prefer_boost is None else float(prefer_boost)
    now = int(time.time())
    words = [w for w in (tags_hint or []) if w]
    t = str(text or "")
    _t0 = t.strip()
    for _k, _v in _MOOD_SYN.items():          # 情绪词扩展（"无语"→尴尬/离谱/嫌弃…）
        if not _k:
            continue
        if _k in _t0 or any((w in _t0) for w in _v.split() if len(w) >= 2):
            t += " " + _k + " " + _v
    words = words + [w for w in t.split() if w and w not in words]
    scored = []
    for it in items:
        if str(it.get("id")) in ex:
            continue
        score = 0
        for tag in (it.get("tags") or []) + [it.get("desc") or ""]:
            if tag and tag in t:
                score += 2
        for w in words:
            if w and (w in (it.get("tags") or []) or w in (it.get("desc") or "")):
                score += 1
        if it.get("last_used") and now - int(it["last_used"]) < 86400:
            score -= 6                     # 24 小时内用过的明显靠后（真人换着发）
        if it.get("last_used") and now - int(it["last_used"]) < 3600:
            score -= 6                     # 1 小时内用过的更靠后
        if str(it.get("id")) in pset:
            score += pboost                  # 她自己的形象：更容易被挑中（可配）
        if score > 0:
            scored.append((score, it))
    # 第二排序键：优先「最久没用过的」（而不是用得多的一直被挑中）
    scored.sort(key=lambda x: (-x[0], int(x[1].get("last_used") or 0)))
    return [x[1] for x in scored[:limit]]


def find(sid: str):
    """按 id 精确找一张表情（支持大小写不一致、带不带引号都行）。"""
    key = str(sid or "").strip().strip("\"'")
    if not key:
        return None
    items = load()
    for it in items:
        if str(it.get("id", "")) == key:
            return it
    low = key.lower()
    for it in items:
        if str(it.get("id", "")).lower() == low:
            return it
    return None


def set_note(sid: str, note: str, tags=None) -> bool:
    """改一张表情的备注/标签（她自己给表情写备注用）。"""
    items = load()
    hit = False
    for it in items:
        if str(it.get("id")) == str(sid):
            if str(note or "").strip():
                it["desc"] = str(note).strip()[:60]
            if tags:
                it["tags"] = [str(t)[:8] for t in list(tags)[:6]]
            hit = True
            break
    if hit:
        save(items)
    return hit


def mark_used(sid: str) -> None:
    items = load()
    for it in items:
        if str(it.get("id")) == str(sid):
            it["used"] = int(it.get("used") or 0) + 1
            it["last_used"] = int(time.time())
            break
    save(items)


def add_file(path: str, group: str = "", desc: str = "", tags=None, h: str = "",
             max_store: int = 300, ge=None) -> dict | None:
    """把一张图收进收藏夹（去重、限量）。返回入库条目，重复/失败返回 None。"""
    try:
        size = os.path.getsize(path)
    except Exception:
        return None
    try:                                   # 单文件上限可配（QQ 动图表情常有 2~3MB）
        import json as _json, os as _os
        _mb = float((_json.load(open(_os.path.join(HERE, "config.json"), encoding="utf-8"))
                     .get("stickers") or {}).get("max_file_mb", 2) or 2)
    except Exception:
        _mb = 2.0
    if size <= 0 or size > _mb * 1024 * 1024:
        return None
    h = h or ahash(path)
    items = load()
    if h and any(str(x.get("hash")) == h for x in items):
        return None
    ext = (os.path.splitext(path)[1] or ".png").lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        ext = ".png"
    sid = "s%d" % int(time.time() * 1000)
    fn = sid + ext
    os.makedirs(DATA, exist_ok=True)
    try:
        import shutil
        shutil.copyfile(path, os.path.join(DATA, fn))
    except Exception:
        return None
    it = {"id": sid, "file": fn, "hash": h, "desc": desc or "", "tags": list(tags or []),
          "group": str(group or ""), "added_at": int(time.time()), "used": 0, "last_used": 0,
          "size": size, "token": int((ge or {}).get("token") or 0),
          # 收图时的识图结论，方便事后审计（哪些是照片混进来的）
          "kind": str((ge or {}).get("kind") or ""), "style": str((ge or {}).get("style") or ""),
          "fun": (ge or {}).get("fun"), "src": str((ge or {}).get("src") or "")}
    items.append(it)
    if len(items) > int(max_store or 300):
        items.sort(key=lambda x: (int(x.get("used") or 0), int(x.get("last_used") or x.get("added_at") or 0)))
        for old in items[: len(items) - int(max_store or 300)]:
            try:
                os.remove(abs_path(old))
            except Exception:
                pass
        items = items[len(items) - int(max_store or 300):]
    save(items)
    return it


def delete(sid: str) -> bool:
    items = load()
    keep, hit = [], False
    for it in items:
        if str(it.get("id")) == str(sid):
            hit = True
            try:
                os.remove(abs_path(it))
            except Exception:
                pass
        else:
            keep.append(it)
    if hit:
        save(keep)
    return hit


def stats() -> dict:
    items = load()
    return {"count": len(items), "bytes": sum(int(x.get("size") or 0) for x in items),
            "tags": sorted({t for x in items for t in (x.get("tags") or [])})[:30]}


def _hash_close(h: str, others, tol: int = 8) -> bool:
    """aHash 比较：相等或汉明距离 <= tol 都算同一张（QQ 那边会重编码）。"""
    def _bits(x):
        try:
            return bin(int(str(x), 16))[2:].zfill(64)
        except Exception:
            return ""

    b = _bits(h)
    if not b:
        return False
    for o in others or []:
        ob = _bits(o)
        if not ob:
            continue
        if b == ob:
            return True
        if sum(1 for x, y in zip(b, ob) if x != y) <= tol:
            return True
    return False


def reconcile_deletions(uin: str = "", limit: int = 120, dry: bool = False,
                        grace_sec: int = 600) -> dict:
    """以 QQ 收藏面板为准：面板里没有了的（我们推上去的）表情，本地也删掉。

    - 面板列表拿不到/为空 → 直接放弃（绝不因为接口故障清库）
    - 只处理"我们推上去过"的条目（face_pushed 或 src=account），本地原生收藏不受影响
    - 刚加的（grace_sec 内）跳过，避免推送还没生效就被判成"被删"
    - 删除前把图复制到 data/stickers/_rejected/（可回滚）
    """
    import shutil
    urls = fetch_faces(uin, count=int(limit))
    if not urls:
        return {"error": "面板返回为空，放弃同步（防误删）"}
    panel_hashes = []
    got = 0
    for u in urls[:limit]:
        try:
            p = _download_remote(u)
            if not p:
                continue
            h = ahash(p)
            if h:
                panel_hashes.append(h)
            got += 1
            try:
                os.remove(p)
            except Exception:
                pass
        except Exception:
            continue
    if got < max(3, len(urls) // 5):
        return {"error": "面板图大部分下载失败（%d/%d），放弃同步" % (got, len(urls))}
    items = load()
    now = int(time.time())
    removed, kept, skipped = [], [], 0
    for it in items:
        if not (it.get("face_pushed") or it.get("src") == "account"):
            continue
        if now - int(it.get("added_at") or 0) < int(grace_sec):
            skipped += 1
            continue
        h = ""
        p = abs_path(it) or ""
        if not p or not os.path.isfile(p):
            try:
                p = ensure_local(it)
            except Exception:
                p = ""
        if p and os.path.isfile(p):
            h = ahash(p)                      # 一律重算：旧条目的 hash 早期可能退化成 md5
        else:
            h = str(it.get("hash") or "")
        if h and _hash_close(h, panel_hashes):
            continue
        removed.append(it.get("id"))
        if not dry:
            try:
                p = abs_path(it)
                if p and os.path.isfile(p):
                    os.makedirs(os.path.join(DATA, "_rejected"), exist_ok=True)
                    shutil.copy2(p, os.path.join(DATA, "_rejected", os.path.basename(p)))
            except Exception:
                pass
            try:
                delete(it.get("id"))
            except Exception:
                pass
    return {"panel": len(urls), "panel_hashed": got, "removed": len(removed),
            "removed_ids": removed[:20], "recent_skipped": skipped,
            "left": len(load()) if not dry else len(items)}


# ---------------- 面板审核 / 清理 ----------------

def fetch_face_detail(uin: str = "", count: int = 300) -> list:
    """面板表情详情：[{emoji_id, md5, url, desc}]（md5 与原图一致，可当配对键）。"""
    d = panel("fetch_custom_face_detail", {"count": int(count)}, uin)
    if isinstance(d, dict):
        d = d.get("items") or list(d.values())
    out = []
    for x in (d or []):
        if isinstance(x, dict) and x.get("emoji_id"):
            out.append({"emoji_id": str(x.get("emoji_id")),
                        "md5": str(x.get("md5") or "").lower(),
                        "url": str(x.get("url") or ""),
                        "desc": str(x.get("desc") or "")})
    return out


def delete_face(emoji_id: str, uin: str = "") -> bool:
    """从那个 QQ 号的收藏面板删掉一张表情。

    注意：delete_custom_face 成功时 data 是空的（panel() 会返回 {} → 看着像失败），
    所以删完要**复核**面板列表里那张是不是真没了。
    """
    eid = str(emoji_id or "").strip()
    if not eid:
        return False
    panel("delete_custom_face", {"emoji_id": eid}, uin)
    try:
        left = {f.get("emoji_id") for f in fetch_face_detail(uin, 300)}
        if left:
            return eid not in left
    except Exception:
        pass
    return False


def face_pairs(uin: str = "", count: int = 300) -> dict:
    """本地库 ↔ 面板配对：{md5: {"local": item|None, "face": detail}}。

    用 md5 精确配对（面板返回的 md5 就是原图 md5，实测 55/55 全中）。
    QQ 偶尔会重编码导致 md5 不同——那种情况 reconcile_deletions 会用 aHash 兜底，
    这里不重算 aHash（省一次全量下载）。
    """
    import hashlib
    faces = fetch_face_detail(uin, count)
    by_md5 = {}
    for f in faces:
        if f.get("md5"):
            by_md5.setdefault(f["md5"], {"local": None, "face": f})
    for it in load():
        pth = abs_path(it)
        if not pth or not os.path.isfile(pth):
            continue
        try:
            h = hashlib.md5(open(pth, "rb").read()).hexdigest().lower()
        except Exception:
            continue
        if h in by_md5:
            by_md5[h]["local"] = it
        else:
            by_md5.setdefault(h, {"local": it, "face": None})
    return by_md5


def _face_image(path: str, url: str) -> str:
    """拿到能识别的本地文件：优先本地库里的，否则下载面板图。"""
    if path and os.path.isfile(path):
        return path
    if url:
        try:
            return _download_remote(url) or ""
        except Exception:
            return ""
    return ""


def audit_faces(vision: bool = True, uin: str = "", count: int = 300,
                progress=None) -> dict:
    """按新规则复判面板里每一张表情。返回 {items, suggest_del, ...}。

    只判断"值不值得留"，不动手删——删由 delete_face 单独做（决定权在人）。
    """
    pairs = face_pairs(uin, count)
    rows, bad = [], []
    for h, v in pairs.items():
        it, face = v.get("local"), v.get("face")
        pth = abs_path(it) if it else ""
        row = {"md5": h,
               "local_id": (it or {}).get("id", ""),
               "emoji_id": (face or {}).get("emoji_id", ""),
               "desc": (it or {}).get("desc", "") or (face or {}).get("desc", ""),
               "tags": "/".join((it or {}).get("tags") or []),
               "face": bool(face), "local": bool(it),
               "long": image_size(pth) if (pth and os.path.isfile(pth)) else 0,
               "verdict": "", "why": "", "kind": "", "style": "", "fun": ""}
        if vision:
            img = _face_image(pth, (face or {}).get("url", ""))
            if not img:
                row["verdict"], row["why"] = "?", "拿不到图，没法判"
            else:
                g = tag_image(img)
                keep, why = judge_collect(g, sub_type=1, long_side=row["long"])
                row.update({"verdict": "留" if keep else "删", "why": why,
                            "kind": g.get("kind", ""), "style": g.get("style", ""),
                            "fun": g.get("fun", "")})
                if not keep:
                    bad.append(row)
            try:
                if img.startswith("/tmp/"):
                    os.remove(img)
            except Exception:
                pass
        rows.append(row)
        if progress:
            try:
                progress(row)
            except Exception:
                pass
    return {"rows": rows, "suggest_del": bad, "total": len(rows),
            "panel": sum(1 for r in rows if r["face"]),
            "local_only": sum(1 for r in rows if r["local"] and not r["face"]),
            "panel_only": sum(1 for r in rows if r["face"] and not r["local"])}


# ---------------- 审阅页（给主人筛备注用） ----------------

# 备注里出现这些词，说明大概率是"按画面瞎猜的态度"，值得人再核一遍
_SUSPECT_WORDS = ("敷衍", "尴尬", "无语", "阴阳", "冷漠", "震惊", "可怜", "委屈", "害羞",
                  "看起来", "像在", "似乎在", "应该是", "估计是", "大概在", "好像在")
_THUMB_MAX = 180
_THUMB_QUALITY = 78


def _thumb_b64(path: str) -> str:
    """缩略图 → 内嵌用 base64（失败返回空串）。"""
    try:
        import base64
        import io as _io
        from PIL import Image as _PIL
        im = _PIL.open(path)
        try:
            im.seek(0)
        except Exception:
            pass
        im = im.convert("RGB")
        w, h = im.size
        k = min(_THUMB_MAX / float(max(1, w)), _THUMB_MAX / float(max(1, h)), 1.0)
        if k < 1.0:
            im = im.resize((max(1, int(w * k)), max(1, int(h * k))), _PIL.LANCZOS)
        buf = _io.BytesIO()
        im.save(buf, format="JPEG", quality=_THUMB_QUALITY)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def _suspect(it: dict) -> bool:
    """这条备注值不值得人再看一眼。"""
    d = str(it.get("desc") or "").strip()
    if not d or len(d) <= 8:
        return True
    return any(w in d for w in _SUSPECT_WORDS)


def review_html(out: str = "") -> dict:
    """生成审阅页。返回 {path, count, suspect, size}。"""
    import html as _html
    import time as _t
    items = [x for x in load() if x.get("id")]
    if not out:
        out = os.path.join(DATA, "review.html")
        # 改名成带日期的，方便对比（旧文件留着不管）
    rows, susp = [], []
    for it in items:
        p = abs_path(it) or ""
        b64 = _thumb_b64(p) if p and os.path.isfile(p) else ""
        d = str(it.get("desc") or "").strip()
        _tags = [str(x) for x in (it.get("tags") or [])]
        _cmd = 'qqbot-sticker note %s "%s" %s' % (it.get("id"), d or "新备注",
                                                  ",".join(_tags) if _tags else "")
        row = {"id": str(it.get("id")), "desc": d or "（没有备注）", "tags": _tags,
               "used": int(it.get("used") or 0), "b64": b64, "cmd": _cmd,
               "added": _t.strftime("%m-%d", _t.localtime(int(it.get("added_at") or 0))),
               "group": str(it.get("group") or "")}
        (susp if _suspect(it) else rows).append(row)
    rows.sort(key=lambda r: -r["used"])
    susp.sort(key=lambda r: -r["used"])
    body = []
    for title, arr, note in (
            ("① 先看这些（备注像「按画面猜的」，或太短）", susp,
             "这些最可能是错的：只描述了画面/态度，没写「这图拿来干嘛」。想好用法就复制下面的命令改。"),
            ("② 其余（备注看着还行，可顺手过一遍）", rows, "")):
        body.append('<h2>%s <span class=sub>%d 张</span></h2>' % (title, len(arr)))
        if note:
            body.append('<p class=hint>%s</p>' % note)
        body.append('<div class=grid>')
        for r in arr:
            img = ('<img src="data:image/jpeg;base64,%s">' % r["b64"]) if r["b64"]                 else '<div class=noimg>没图</div>'
            body.append(
                '<div class=card>%s<div class=meta><div class=desc>%s</div>'
                '<div class=line><code>%s</code> · 用过 %d · %s</div>'
                '<div class=tags>%s</div>'
                '<pre>%s</pre></div></div>'
                % (img, _html.escape(r["desc"]),
                   _html.escape(r["id"]), r["used"], _html.escape(r["added"]),
                   "".join('<span class=tag>%s</span>' % _html.escape(t) for t in r["tags"]),
                   _html.escape(r["cmd"])))
        body.append('</div>')
    doc = """<!doctype html><html lang=zh><meta charset=utf-8>
<title>大肥鱼表情库审阅（%d 张）</title>
<style>
body{font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:24px;background:#fafafa;color:#222}
h1{font-size:20px;margin:0 0 4px} h2{font-size:16px;margin:26px 0 6px}
.sub{color:#888;font-weight:400;font-size:13px} .hint{color:#a35;margin:2px 0 10px;font-size:13px}
.grid{display:flex;flex-wrap:wrap;gap:14px}
.card{width:330px;background:#fff;border:1px solid #e3e3e3;border-radius:10px;padding:10px;display:flex;gap:10px}
.card img{width:120px;height:120px;object-fit:contain;background:#f0f0f0;border-radius:6px;flex:none}
.noimg{width:120px;height:120px;display:flex;align-items:center;justify-content:center;background:#f0f0f0;color:#999;border-radius:6px;flex:none;font-size:12px}
.meta{min-width:0;flex:1} .desc{font-weight:600;margin-bottom:2px;word-break:break-word}
.line{color:#777;font-size:12px} code{background:#f3f3f3;padding:0 3px;border-radius:3px}
.tag{display:inline-block;background:#eef4ff;color:#2a5db0;border-radius:4px;padding:0 5px;margin:2px 3px 0 0;font-size:12px}
pre{margin:6px 0 0;background:#f7f7f7;border:1px solid #eee;border-radius:6px;padding:5px 6px;font-size:11px;white-space:pre-wrap;word-break:break-all}
</style>
<h1>大肥鱼表情库审阅 · %d 张</h1>
<p class=hint>每张卡片下面是可以直接复制到 SSH 里执行的命令（改备注 / 改标签）。改完不用重启，她下次选图就用新备注。</p>
%s
</html>""" % (len(items), len(items), "\n".join(body))
    os.makedirs(DATA, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    return {"path": out, "count": len(items), "suspect": len(susp),
            "size": os.path.getsize(out)}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="表情包收藏夹")
    ap.add_argument("cmd", choices=["list", "stats", "add", "del", "tag", "sync", "faces",
                                    "history", "push", "mirror", "audit", "rmface",
                                    "faceinfo", "review", "note"], nargs="?", default="stats")
    ap.add_argument("arg", nargs="?", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--group", default="")
    a = ap.parse_args()
    if a.cmd == "add":
        p = a.arg
        if not os.path.exists(p):
            print("文件不存在:", p); return 1
        g = tag_image(p)
        if not g.get("is_meme"):
            print("识图认为不适合当表情包，未收录:", g); return 2
        it = add_file(p, group=a.group, desc=g.get("desc", ""),
                      tags=(a.tags.split(",") if a.tags else g.get("tags")), ge=g)
        print("已收录:", it or "重复或失败")
        return 0
    if a.cmd == "del":
        print("删除", a.arg, "→", delete(a.arg))
        return 0
    if a.cmd == "tag":
        print(tag_image(a.arg))
        return 0
    if a.cmd == "faces":
        urls = fetch_faces(count=int(a.tags or 100))
        print("账号表情 %d 张：" % len(urls))
        for u in urls[:20]:
            print("  ", u)
        return 0
    if a.cmd == "sync":
        n = sync_account(limit=int(a.tags or 200))
        print("同步完成，新增", n, "张；", stats())
        return 0
    if a.cmd == "history":
        n = import_history(a.arg or "869622030", limit=int(a.tags or 40))
        print("从历史消息里收了", n, "张；", stats())
        return 0
    if a.cmd == "mirror":
        _dry = ("dry" in (a.tags or "")) or (str(a.arg).strip().lower() == "dry")
        r = reconcile_deletions(dry=_dry, limit=120)
        print("反向同步（以 QQ 面板为准）：", r)
        if not _dry:                     # 顺手把"上次没推上去"的补推（偶发失败自愈）
            try:
                _n = push_pending()
                if _n:
                    print("补推面板:", _n, "张")
            except Exception as _e:
                print("补推面板失败:", _e)
        return 0
    if a.cmd == "faceinfo":
        for f in fetch_face_detail(count=int(a.tags or 300)):
            print("%-58s %s %s" % (f["emoji_id"], f["md5"][:12], f["desc"]))
        return 0
    if a.cmd == "rmface":
        ids = [x for x in str(a.arg or "").split(",") if x.strip()]
        if not ids:
            print("用法：qqbot-sticker rmface <emoji_id>[,<emoji_id>...]")
            return 1
        n = 0
        for eid in ids:
            ok = delete_face(eid)
            print("%s %s" % ("删除" if ok else "删除失败（id 不对或面板抽风）", eid))
            n += 1 if ok else 0
            time.sleep(0.4)
        print("面板删除 %d/%d 张；跑一次 qqbot-sticker mirror 同步本地" % (n, len(ids)))
        return 0
    if a.cmd == "audit":
        _v = "novision" not in (a.tags or "")
        if _v:
            print("逐张识图复判（%s 张，慢，喝口水）…" % "面板")
        _cnt = 300
        try:
            _cnt = int(a.group) if str(a.group or "").strip() else 300
        except Exception:
            _cnt = 300
        r = audit_faces(vision=_v, count=_cnt,
                        progress=(lambda row: print("  %-4s %-8s fun=%-3s %-16s %s"
                                                    % (row["verdict"], row["kind"],
                                                       row["fun"], row["local_id"] or "-",
                                                       str(row["desc"])[:22]),
                                  flush=True) if _v else None))
        print()
        print("面板 %d 张 / 只在本地 %d / 只在面板 %d" % (r["panel"], r["local_only"],
                                                         r["panel_only"]))
        try:
            import json as _j
            _o = os.path.join(HERE, "data", "sticker_audit.json")
            os.makedirs(os.path.dirname(_o), exist_ok=True)
            _j.dump(r, open(_o, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("完整结果写到", _o)
        except Exception as _e:
            print("写 json 失败:", _e)
        if _v:
            print("建议删掉 %d 张：" % len(r["suggest_del"]))
            for row in r["suggest_del"]:
                print("  %-8s fun=%-3s %-18s %s ｜ %s"
                      % (row["kind"], row["fun"], str(row["desc"])[:20], row["why"],
                         row["emoji_id"]))
        return 0
    if a.cmd == "review":
        _out = str(a.group or "").strip()
        r = review_html(_out)
        print("审阅页已生成：%s" % r["path"])
        print("  共 %d 张，其中 %d 张建议先看（备注像「按画面猜的」）。文件 %.1f MB。"
              % (r["count"], r["suspect"], r["size"] / 1048576.0))
        print("  在自己电脑上看：")
        print("    scp root@<服务器>:%s ." % r["path"])
        print("  或在服务器上临时开个只监听本机的网页服务（别开公网）：")
        print("    python3 -m http.server 8899 --bind 127.0.0.1 --directory %s"
              % os.path.dirname(r["path"]))
        print("    然后 ssh -L 8899:127.0.0.1:8899 root@<服务器>，浏览器开 http://127.0.0.1:8899/review.html")
        return 0
    if a.cmd == "note":
        _sid = str(a.arg or "").strip()
        _note = str(a.tags or "").strip()
        _tags = [x for x in str(a.group or "").split(",") if x.strip()]
        if not _sid:
            print("用法：qqbot-sticker note <id> <备注文字> [标签1,标签2]")
            return 1
        if not set_note(_sid, _note, _tags or None):
            print("没找到这张：%s" % _sid)
            return 1
        _it = find(_sid) or {}
        print("已改：%s《%s》标签=%s" % (_sid, _it.get("desc"), ",".join(_it.get("tags") or [])))
        return 0
    if a.cmd == "push":
        n = push_to_face(int(a.tags or 50))
        print("推进 QQ 表情收藏:", n, "张")
        return 0
    if a.cmd == "list":
        for it in load():
            print("%-10s %-30s %s" % (it.get("id"), it.get("desc"), ",".join(it.get("tags") or [])))
        return 0
    print(stats())
    return 0


if __name__ == "__main__":
    sys.exit(main())
