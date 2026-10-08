# -*- coding: utf-8 -*-
"""未完成事项（她说过的"我再看看/我再查查"）：落盘 + 下次注入提示词 + 过会儿自己回来补。

为什么需要：她的长期记忆只记"查证结论"，那条读完像"已完成"；承诺本身没地方存，
  于是别人问「你怎么不继续说了」时，她手里只有聊天原文，容易当成闲聊糊过去。
"""
import time

TTL = 1800          # 30 分钟没做完就算了（别一直念着）


def fresh(ts, ttl: int = TTL) -> bool:
    """这条未完成事项还算数吗。"""
    try:
        return (time.time() - float(ts or 0)) <= float(ttl or TTL)
    except Exception:
        return False


def hint(question: str, promise: str) -> str:
    """给模型看的提示：把"你答应过的事"明确摆出来。"""
    q = str(question or "").strip()[:80]
    p = str(promise or "").strip()[:60]
    if not p:
        return ""
    if q:
        return ("【你之前没做完的事】他问过「%s」，你说过「%s」但还没把结果给出来。"
                "有人问「你怎么不继续说了 / 然后呢」就是指这件事：把它查完补上；"
                "确实查不到就明确说没查到。" % (q, p))
    return ("【你之前没做完的事】你说过「%s」但还没把结果给出来，现在把它做完补上。"
            % (p,))


def merge(old: dict, key: str, question: str, promise: str, limit: int = 50) -> dict:
    """写入一条未完成事项（纯函数，方便离线测）。"""
    d = dict(old or {})
    d[str(key)] = {"q": str(question or "")[:120], "p": str(promise or "")[:120],
                   "ts": int(time.time())}
    if len(d) > limit:
        _s = sorted(d.items(), key=lambda kv: int((kv[1] or {}).get("ts") or 0))
        d = dict(_s[-limit:])
    return d
