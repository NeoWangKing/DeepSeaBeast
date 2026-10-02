"""偶尔"接着自己刚发的那句话再补一句"。

像真人一样：说完主回复，过两三秒又想起点什么，补一条。走记忆系统那个便宜模型出口
（memory.llm），不占主模型额度；概率和每小时上限由 config.json 的 followup 块控制。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
def _llm():
    """延迟导入：插件热重载时少一层导入链，避免和主模块抢同一个包。"""
    try:
        from memory import llm as _l
        return _l
    except Exception:
        return None

PROMPT = """你是 QQ 里的一个真人网友。你刚刚发了这么一句：

「%s」

现在你想像真人那样，紧接着再补一条（补充说明 / 想起个细节 / 加个前提 / 自嘲 / 小声嘀咕）。
要求：
- 1~20 个字，纯口语，像随手补的。
- 不要重复刚才的意思，不要再提问，不要解释"我在补充"。
- 不要引号，不要表情标记。
- 确实没什么好补的，就只回两个字：不用。
只输出你要补的那句话本身。"""

BAD = {"不用", "无", "没有", "算了", "略", "同上"}


def make_note(prev: str, cfg: dict | None = None, max_chars: int = 40) -> str:
    """返回一句补充，或空串（表示这次不补）。失败一律静默返回空串。"""
    llm = _llm()
    if llm is None:
        return ""
    p = " ".join(str(prev or "").split())
    if len(p) < 4:
        return ""
    try:
        text, _u = llm.chat([{"role": "user", "content": PROMPT % p[:140]}],
                            cfg, max_tokens=60)
    except Exception:
        return ""
    t = " ".join(str(text or "").split()).strip("「」[]()<> 　")
    if not t or t in BAD or len(t) > int(max_chars):
        return ""
    if t in p:
        return ""
    return t
