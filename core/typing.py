"""判断"这句话说完了没有"。

为什么要它：群友经常分几条发（"我跟你" / "说个事啊"）。如果第一条就抢答，
就变成答非所问。人这时候会等一下——但如果已经能猜全意思，也会插嘴。

所以这是个**启发式判断**，不是精确语法分析：

  看不完  ← 结尾是连接词（然后/因为/但是…）、结尾是逗号/省略号、
            或者短得离谱又没有标点和疑问语气（"我跟你"）

  看完了  ← 结尾有句号/问号/感叹号/波浪号，或者就是常见的完整短句（"在吗"/"你好"）

宁可判"说完了"（正常回），也不要乱等——等待只会让对话变慢。
"""

from __future__ import annotations

# 结尾是这些词，说明后面还有话
TAIL_WORDS = (
    "然后", "就是", "因为", "所以", "但是", "不过", "而且", "并且", "如果", "虽然",
    "的话", "那个", "这个", "还有", "以及", "或者", "不然", "否则", "其实", "倒",
    "我", "你", "他", "她", "它", "我们", "你们", "他们",
    "跟", "和", "把", "被", "让", "给", "对", "在", "是", "有", "想", "要", "说",
)
# 结尾是这些符号，说明后面还有话
TAIL_MARKS = ("，", ",", "、", "…", "...", "：", ":")
# 结尾是这些语气词且句子不长 → 一般是完整的问句/应答
COMPLETE_PARTICLES = ("吗", "呢", "吧", "嘛", "啊", "呀", "啦", "哦", "哈", "了")

# 结尾是这些符号，说明说完了
END_MARKS = ("。", "？", "?", "！", "!", "~", "～", "）", ")", "”", "\"")
# 短但完整的常见话
COMPLETE_SHORT = {
    "在吗", "在不在", "在么", "你好", "您好", "谢谢", "好的", "好", "行", "嗯", "哦",
    "哈哈", "哈哈哈", "收到", "ok", "okay", "人呢", "有人吗", "早", "晚安", "拜拜",
    "笑死", "牛逼", "牛", "服了", "确实", "真的", "对", "是的", "算了", "没事",
}


def looks_unfinished(text: str, min_short: int = 6) -> bool:
    """True 表示"看起来还没说完，最好等一下"。"""
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    if low in COMPLETE_SHORT:
        return False
    if t.endswith(END_MARKS):
        return False
    if len(t) <= 8 and t.endswith(COMPLETE_PARTICLES):
        return False          # "然后呢" / "吃了吗" / "走吧"
    if t.endswith(TAIL_MARKS):
        return True
    if t.endswith(TAIL_WORDS):
        return True
    # 很短的、没标点、也不在"完整短句"表里 → 大概率还有下文
    if len(t) <= min_short and not any(ch in t for ch in "。？！?!~～"):
        return True
    return False


def should_wait(text: str, has_more_hint: bool = False) -> bool:
    """综合判断：要不要等她说完再回。

    has_more_hint：调用方已知"她刚发过一条、还没回"（比如合并缓冲里还有内容）。
    """
    if has_more_hint:
        return True
    return looks_unfinished(text)
