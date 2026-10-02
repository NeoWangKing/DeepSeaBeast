"""海龟汤题库：汤面（玩家看到的）+ 汤底（真相，绝不能泄露）。

选题标准（血泪教训）：
1. **逻辑必须自洽**——汤底里每个「为什么」都能自证，不需要额外假设；
2. **汤面不能有误导性废话**——不写和真相无关的细节（不然玩家一直问，答「不重要」很挫败）；
3. **汤底 40~120 字**，讲完能让人「哦——原来如此」；
4. 优先选流传广、口碑好的经典题；宁缺毋滥，题目少也没关系。

加题就往下加一条；id 用短英文。
"""

# 类别：群友可以点名「来个XX的」，AI 现编时也会按这个类别写
CATEGORIES = {
    "horror":  {"label": "恐怖惊悚", "words": ["恐怖", "惊悚", "吓人", "灵异", "阴森", "鬼故事", "鬼"],
                "desc": "要有明确的诡异/威胁/细思极恐元素（未知的存在、被人盯着、超自然、被害感）。"
                        "只是悲伤、意外死亡、生病、破案的，不算恐怖"},
    "mystery": {"label": "悬疑推理", "words": ["悬疑", "推理", "烧脑", "逻辑", "破案", "侦探", "解谜"],
                "desc": "核心是一个要一步步推出来的逻辑谜题，偏本格推理"},
    "warm":    {"label": "温情治愈", "words": ["温情", "治愈", "感人", "暖心", "温馨", "催泪", "感动"],
                "desc": "真相是善意/误会/亲情，读完心里一暖，不吓人"},
    "funny":   {"label": "欢乐荒诞", "words": ["搞笑", "欢乐", "沙雕", "无脑", "轻松", "荒诞", "好笑", "逗"],
                "desc": "谜底荒诞好笑、反转出人意料，读完好笑而不是害怕"},
}
DEFAULT_CATEGORY = "mystery"


def cat_label(cid: str) -> str:
    return (CATEGORIES.get(str(cid or DEFAULT_CATEGORY)) or CATEGORIES[DEFAULT_CATEGORY])["label"]


def category_of(pz: dict) -> str:
    cid = str((pz or {}).get("category") or "")
    return cid if cid in CATEGORIES else DEFAULT_CATEGORY


def detect_category(text: str) -> str:
    """从群友的话里认出他想玩哪一类（认不出返回空串）。"""
    t = str(text or "")
    for cid, meta in CATEGORIES.items():
        for w in meta.get("words") or []:
            if w and w in t:
                return cid
    return ""


PUZZLES = [
    {
        "id": "turtle-soup",
        "category": "horror",
        "title": "海龟汤",
        "surface": "一个男人走进餐馆，点了一份海龟汤。他只喝了一口，就放下勺子，付钱回家自杀了。为什么？",
        "bottom": "多年前他和同伴在海上遇难，漂流多日。同伴端来一碗汤说抓到了海龟，他喝下才活了下来。"
                  "今天他第一次喝到真正的海龟汤，发现味道完全不同——当年那碗「海龟汤」其实是同伴用"
                  "自己身上的肉煮的。同伴用自己的命换了他一命，他承受不住这个真相。",
    },
    {
        "id": "water-gun",
        "category": "funny",
        "title": "酒吧里的水和枪",
        "surface": "一个男人走进酒吧，却只要了一杯白开水。酒保二话不说掏出一把枪指着他。男人说了声谢谢就走了。为什么？",
        "bottom": "男人一直在打嗝，想喝口水把嗝压下去。酒保一眼看出他在打嗝，掏枪吓了他一跳，嗝当场"
                  "就停了。男人道了谢便离开。",
    },
    {
        "id": "elevator",
        "category": "mystery",
        "title": "只到七楼",
        "surface": "一个男人住在十楼。每天早上他都坐电梯到一楼去上班；晚上回来时，如果电梯里有人，他就直接"
                   "坐到十楼，如果只有他一个人，他只坐到七楼，再走楼梯上去。为什么？",
        "bottom": "他个子很矮，站在电梯里只够得到七楼的按钮，够不到十楼。电梯里有人的时候，他就可以"
                  "请对方帮他按一下十楼。",
    },
    {
        "id": "half-match",
        "category": "mystery",
        "title": "半根火柴",
        "surface": "沙漠中央躺着一个人，已经死了。四周没有任何脚印，他手里紧紧攥着半根火柴。他是怎么死的？",
        "bottom": "他和几个人一起坐热气球飞越沙漠，中途气球超重、不停下坠，必须有人跳下去。大家抽火柴"
                  "决定谁跳，他抽到了最短的那半根，于是从热气球上跳了下来。",
    },
    {
        "id": "funeral",
        "category": "horror",
        "title": "葬礼上的陌生人",
        "surface": "一个女孩在母亲的葬礼上遇到一个从没见过的男人，两人一见钟情。可是几个月后，她亲手杀死了自己的姐姐。为什么？",
        "bottom": "她觉得那个男人是来参加葬礼的亲友，只有在葬礼上才有可能再见到他。为了再办一场葬礼"
                  "把那个男人引出来，她杀死了姐姐。",
    },
    {
        "id": "surgeon",
        "category": "mystery",
        "title": "手术室门口的医生",
        "surface": "一对父子遭遇车祸，父亲当场死亡，男孩被紧急送进医院。外科医生赶到手术室门口，看了一眼男孩说："
                   "我不能给他做手术，他是我儿子。这是怎么回事？",
        "bottom": "外科医生是男孩的母亲。出车祸的是男孩和他父亲，母亲赶来给儿子做手术——只是大家听到"
                  "「外科医生」就默认是男性，所以一时想不到这一层。",
    },
    {
        "id": "parachute",
        "category": "mystery",
        "title": "没打开的降落伞",
        "surface": "一名跳伞者从飞机上跳下后坠落身亡。他的降落伞从没被打开过，同伴们却都安全落地。为什么？",
        "bottom": "跳伞前大家匆忙收拾装备，他和同伴拿错了包——他背上的是同伴的旅行包，里面根本没有降落伞；"
                  "而同一个人背走的是他的伞包。等他发现背上没有开伞拉环，已经来不及了。",
    },
    {
        "id": "poison-ice",
        "category": "mystery",
        "title": "一模一样的酒",
        "surface": "两个男人在酒馆里各点了一杯一模一样的酒，边喝边聊。散场后其中一个当晚死了，另一个却毫发无伤。"
                   "酒里没有毒，酒杯也没有毒。为什么？",
        "bottom": "毒是下在冰块里的。死去的那个人喝得很慢，冰块慢慢化开，毒才融进酒里被他喝下；另一个"
                  "喝得快，没等冰块化开就把酒喝完了。",
    },
]


import json as _json
import os as _os

# AI 自己编的题：data/games/turtle_soup/ai_puzzles.json（由 turtle/gen.py 生成并自评合格）
_AI_FILE = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
                         "data", "games", "turtle_soup", "ai_puzzles.json")
_ai_cache = {"mtime": None, "items": []}


def ai_puzzles() -> list:
    """读 AI 题库（按文件修改时间缓存，改了文件不用重启）。"""
    try:
        mt = _os.path.getmtime(_AI_FILE)
    except Exception:
        _ai_cache["mtime"], _ai_cache["items"] = None, []
        return []
    if _ai_cache["mtime"] != mt:
        try:
            with open(_AI_FILE, encoding="utf-8") as f:
                d = _json.load(f)
            items = [x for x in (d.get("items") or []) if x.get("surface") and x.get("bottom")]
        except Exception:
            items = []
        _ai_cache["mtime"], _ai_cache["items"] = mt, items
    return list(_ai_cache["items"])


_WEB_FILE = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
                          "data", "games", "turtle_soup", "web_puzzles.json")
_web_cache = {"mtime": None, "items": []}


def web_puzzles() -> list:
    """网上题库导入的题（data/games/turtle_soup/web_puzzles.json，由 import_web.py 生成）。"""
    try:
        mt = _os.path.getmtime(_WEB_FILE)
    except Exception:
        _web_cache["mtime"], _web_cache["items"] = None, []
        return []
    if _web_cache["mtime"] != mt:
        try:
            with open(_WEB_FILE, encoding="utf-8") as f:
                d = _json.load(f)
            items = [x for x in (d.get("items") or []) if x.get("surface") and x.get("bottom")]
        except Exception:
            items = []
        _web_cache["mtime"], _web_cache["items"] = mt, items
    return list(_web_cache["items"])


def all_puzzles(include_web: bool = True) -> list:
    """人工题库 + AI 题库（+ 网络导入题库）。"""
    ps = list(PUZZLES) + ai_puzzles()
    if include_web:
        ps += web_puzzles()
    return ps


def by_id(pid):
    for p in all_puzzles():
        if p.get("id") == pid:
            return p
    return None


def get(index=None, pid=None):
    """按 id 或下标取题；不传就取第一道。"""
    if pid:
        p = by_id(pid)
        if p:
            return p
    ps = all_puzzles()
    if index is None:
        index = 0
    return ps[index % len(ps)] if ps else None
