"""输出协议解析：模型的"一段输出"→"要发几条、要不要发"。

协议（与 promptlib/sections.py 的【输出协议】一致）：
  · 多条：|||      —— 兼容全角 ｜、中间夹空格的 | |、以及两个以上竖线
  · 沉默：[不说话] / [SILENT] / [潜水] / [不回]
  · 表情：[表情:id]（指名某一张）或 [表情:情绪]（让插件按情绪挑）
  · 换行：当模型用换行分段时也认（可关）

纯函数，不依赖 astrbot，方便离线测试。
"""
import re
from dataclasses import dataclass, field

SILENT_MARKERS = ("[不说话]", "[silent]", "[潜水]", "[不回]", "[不回复]", "[不回话]")
STICKER_RE = re.compile(r"\[表情(?::([^\]\n]{1,24}))?\]")
_TRAIL = "，,、；;：:"


@dataclass
class Parsed:
    silent: bool = False
    parts: list = field(default_factory=list)
    source: str = "single"        # silent | marker | lines | single | empty
    stickers: list = field(default_factory=list)   # 文本里出现过的表情标记参数（原样保留）

    def __len__(self):
        return len(self.parts)

    @property
    def multi(self) -> bool:
        return len(self.parts) > 1


def has_silence(text) -> bool:
    low = str(text or "").lower()
    return any(m in low for m in SILENT_MARKERS)


def sticker_hints(text) -> list:
    """返回文本里所有 [表情...] 的参数（可能是 id，也可能是情绪词，空串表示让插件挑）。"""
    return [(m.group(1) or "").strip() for m in STICKER_RE.finditer(str(text or ""))]


def strip_sticker(text) -> str:
    return STICKER_RE.sub("", str(text or ""))


def clean_part(s: str) -> str:
    t = " ".join(str(s or "").split())
    return t.strip().strip(_TRAIL).strip()


def _split_marker(t: str, marker: str) -> list:
    if not marker:
        return []
    m = str(marker).replace("｜", "|")
    if "|" in m:
        t2 = re.sub(r"\|\s*(?=\|)", "|", t)
        t2 = re.sub(r"\s*\|{2,}\s*", "|||", t2)
    else:
        t2 = t
    if m not in t2:
        return []
    return [x for x in t2.split(m) if x.strip()]


def parse(text, marker: str = "|||", max_parts: int = 5, allow_lines: bool = True,
          silence: bool = True) -> Parsed:
    """解析模型输出。沉默优先：出现沉默标记就整条不发（silence=False 时忽略该协议）。"""
    raw = str(text or "")
    out = Parsed(stickers=sticker_hints(raw))
    if not silence:                      # 关掉沉默协议时，把标记本身也清掉，别把标记当话发出去
        for _m in SILENT_MARKERS:
            raw = re.sub(re.escape(_m), "", raw, flags=re.I)
    if silence and has_silence(raw):
        out.silent = True
        out.source = "silent"
        return out
    t = strip_sticker(raw).strip()
    if not t:
        out.source = "empty"
        return out
    segs = _split_marker(t, marker)
    if len(segs) > 1:
        out.source = "marker"
    elif allow_lines:
        segs = [x for x in re.split(r"[\r\n]+", t) if x.strip()]
        if len(segs) > 1:
            out.source = "lines"
        else:
            segs = [t]
    else:
        segs = [t]
    segs = [clean_part(x) for x in segs]
    segs = [x for x in segs if x]
    if not segs:
        out.source = "empty"
        return out
    if max_parts and len(segs) > int(max_parts):
        segs = segs[:int(max_parts) - 1] + [" ".join(segs[int(max_parts) - 1:])]
    out.parts = segs
    if out.source not in ("marker", "lines"):
        out.source = "single"
    return out


def join_marker_prefix(prompt_hint: str = "") -> str:
    """给排查用的说明文本（可选）。"""
    return prompt_hint or "多条用 ||| 分隔；不想说就整条只输出 [不说话]"

FACE_RE = re.compile(r"\[(?:QQ表情|qq表情|QQ脸|qface|face)\s*[:：]\s*([^\]\n]{1,16})\]")


def has_face_mark(text) -> bool:
    return bool(FACE_RE.search(str(text or "")))


def split_faces(text) -> list:
    """把文字按内联 QQ 表情标记切开：[("text", 文字), ("face", 名字), ...]。"""
    out, pos, raw = [], 0, str(text or "")
    for m in FACE_RE.finditer(raw):
        if m.start() > pos:
            out.append(("text", raw[pos:m.start()]))
        out.append(("face", (m.group(1) or "").strip()))
        pos = m.end()
    if pos < len(raw):
        out.append(("text", raw[pos:]))
    if not out:
        out = [("text", raw)]
    return out

_PROTO_LINE = re.compile(r"^\s*(图|回|回复|答|收)\s*[:：]\s*")


def sanitize(text, strip_bar: bool = True, strip_faces: bool = False,
             strip_stickers: bool = True) -> str:
    """发送前的统一兜底：清掉内部协议标记，**默认保留 [QQ表情:…]**（那是要变成真表情的）。

    - 清：沉默标记、[表情:id]（图片表情标记）、以 图/回/收 开头的那几行、多余的 |||
    - 留：[QQ表情:名字]（strip_faces=True 时才清）
    """
    t = str(text or "")
    for _m in SILENT_MARKERS:
        t = re.sub(re.escape(_m), "", t, flags=re.I)
    if strip_stickers:
        t = STICKER_RE.sub("", t)
    if strip_faces:
        t = FACE_RE.sub("", t)
    if strip_bar:
        t = t.replace("|||", " ").replace("｜｜｜", " ")
    keep = []
    for line in t.splitlines():
        if _PROTO_LINE.match(line):
            continue
        keep.append(line)
    t = "\n".join(keep)
    t = re.sub(r"[ \t]{2,}", " ", t).strip()
    return t

SCHEDULE_RE = re.compile(
    r"(\d+\s*(秒|秒钟|分钟|分|min|s)\s*(之?后|以后)|过一?会儿|等一?会儿|等我回来|等下|待会|一会儿再|later)")


def wants_schedule(text) -> bool:
    """这条消息是不是在要求"过一会儿/几秒后再做某事"。"""
    return bool(SCHEDULE_RE.search(str(text or "")))
