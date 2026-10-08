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

VISION_RULES = """这张图是不是"适合在 QQ 群里当表情包/梗图转发"的图？

算（is_meme=true）：表情包、梗图、沙雕图、可爱动物、搞笑截图（把关键信息涂掉的）、明星/动漫表情包。
不算（is_meme=false）：真人自拍、私人照片、聊天记录截图、证件/工牌、二维码、名片、
                        涉黄涉暴、血腥、广告海报、纯文字长图。

最后再判一次 「worth」：这张图**发出来好不好笑、有没有梗、值不值得收进常用表情包库**？
- worth=true：看见就想笑/有梗/可爱/能拿来接话，包括可爱动物、沙雕图、搞笑截图（关键信息涂掉的）。
- worth=false：普通自拍或私人照片、聊天/网页截图、二维码名片、广告、纯文字长图、风景美食等普通照片、
  画质太糊看不出是什么、没什么梗的随手拍。
- **屏幕截图一律 worth=false**：游戏画面/加载界面、软件或网页界面、聊天记录、系统提示、纯文字长图
  ——这些只是"把屏幕截下来"，不是拿来玩的梗图。只有明显被二次加工成梗（加了吐槽文字/涂掉关键信息当段子）才算 true。
- 注意：**真人照片也可能是表情包**——如果姿势/表情夸张、明显在玩梗（比如瘫在椅子上摆烂、指着镜头笑、
  战队选手照、沙雕自拍），而且是拿来当梗用的，worth 就该判 true。别只因为它"是真人"就判 false。
（is_meme 和 worth 的区别：is_meme 是"这算不算表情包"，worth 是"她该不该把它收进自己收藏夹"。
两个判断要一致：kind 是 meme/animal、is_meme=true 的，只要不糊、不是纯风景/美食/随手拍，worth 一般就是 true；
只有真的没梗、看不出是什么、或只是普通照片才判 false。宁可收下也别把好玩的图漏掉。）

只输出 JSON：{"is_meme": true/false, "worth": true/false, "kind": "meme|selfie|screenshot|qr|ad|animal|other",
"desc": "≤14字描述", "tags": ["3~5个中文标签"]}
（kind 用来说清这是什么：meme=表情包/梗图、selfie=真人自拍或私人照片、screenshot=聊天/网页截图、
qr=二维码名片、ad=广告海报、animal=普通动物照、other=其它）
标签用群里会说的词：无语、笑死、绷不住、疑惑、生气、委屈、吃瓜、摸鱼、躺平、干饭、猫、狗、点赞……"""


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
        return {"is_meme": bool(out.get("is_meme")),
                "worth": (None if _w is None else bool(_w)),
                "kind": str(out.get("kind") or "")[:16],
                "desc": str(out.get("desc") or "")[:30],
                "tags": [str(x)[:8] for x in (out.get("tags") or []) if str(x).strip()][:6],
                "token": int((d.get("usage") or {}).get("total_tokens") or 0)}
    except Exception as e:
        print("[stickers] 识图失败: %r" % (e,))
        return {}


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
                it["desc"] = str(note).strip()[:40]
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
          "size": size, "token": int((ge or {}).get("token") or 0)}
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


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="表情包收藏夹")
    ap.add_argument("cmd", choices=["list", "stats", "add", "del", "tag", "sync", "faces",
                                    "history", "push", "mirror"], nargs="?", default="stats")
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
