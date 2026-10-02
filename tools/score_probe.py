#!/usr/bin/env python3
"""离线试算：给定几种典型群聊场景，打印打分器算出的回复概率。
用法（插件目录下）：python3 score_probe.py
不联网、不调模型、不碰 AstrBot。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scoring import Scorer  # noqa: E402

NOW = time.time()
P = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CASES = [
    ("1 群里安静，路人发一条无关的", dict(text="今天食堂的饭真难吃", recent=[("A", "下午有课吗")],
                                    n_ts=1, last=0, offpeak=True)),
    ("2 她刚说完，同一人接着问（必回档）", dict(text="那你说吃啥", recent=[("A", "今天食堂的饭真难吃"), ("我", "食堂那家黄焖鸡还行啊")],
                                    n_ts=4, last=8, offpeak=True, rto="u1")),
    ("3 别人在接她的话（必回档）", dict(text="黄焖鸡还行？你味觉没问题吧", recent=[("我", "食堂那家黄焖鸡还行啊")],
                                     n_ts=3, last=25, offpeak=True)),
    ("4 群里聊得火热（2 分钟 12 条）", dict(text="所以到底谁去拿外卖", recent=[("A", "谁去拿外卖"), ("B", "我不去"), ("C", "我腿断了")],
                                     n_ts=12, last=0, offpeak=True)),
    ("5 一个人在自言自语刷屏", dict(text="啊啊啊啊我不想写作业", recent=[("A", "作业写不完了"), ("A", "真的写不完了"), ("A", "救命")],
                               n_ts=5, last=0, offpeak=True, flood=True)),
    ("6 深夜 3 点，路人闲聊", dict(text="有人还没睡吗", recent=[("A", "失眠了")], n_ts=2, last=0, offpeak=True, hour=3)),
    ("7 这条在 @ 别人", dict(text="小明你明天来不来", recent=[("A", "明天谁去")], n_ts=3, last=0, offpeak=True, at_other=True)),
    ("8 高峰时段路人闲聊（本来就被硬拦）", dict(text="谁有多的充电宝", recent=[("A", "我手机没电了")], n_ts=2, last=0, offpeak=False)),
    ("9 她 60 秒内刚回过（别连珠炮）", dict(text="那行吧", recent=[("我", "行行行，你说了算")], n_ts=6, last=20, offpeak=True)),
    ("10 问句 + 命中群黑话", dict(text="这是抽象还是真急了", recent=[("A", "他刚才那个操作"), ("B", "太抽象了")],
                              n_ts=5, last=0, offpeak=True)),
    ("11 纯语气（哈哈/6）", dict(text="哈哈哈哈", recent=[("A", "笑死我了")], n_ts=3, last=0, offpeak=True)),
    ("12 连续对话 + 问句 + 热闹（应最高）", dict(text="那你觉得去哪个好？", recent=[("我", "我觉得都行啊"), ("A", "别都行")],
                                       n_ts=9, last=10, offpeak=True)),
]


def main():
    s = Scorer(P, {})
    print("base=%s p_min=%s p_max=%s\n" % (s.opt("base"), s.opt("p_min"), s.opt("p_max")))
    for name, c in CASES:
        if c.get("hour") is not None:
            lt = time.localtime(NOW)[:3]
            now = time.mktime(lt + (c["hour"], 10, 0, 0, 0, 0))
            times = [now - 15 * i for i in range(c["n_ts"])][::-1]
        else:
            now = NOW
            times = [now - 15 * i for i in range(c["n_ts"])][::-1]
        last = (now - c["last"]) if c["last"] else 0.0
        stimes = [(now - 10 * i, "u1") for i in range(4)] if c.get("flood") else []
        p, why = s.probability(
            text=c["text"], recent=c["recent"], msg_times=times, last_reply_ts=last,
            last_reply_to=c.get("rto", ""), quotes_me=bool(c.get("quote_me")),
            sender_id="u1", sender_times=stimes, mentions_other=bool(c.get("at_other")),
            sender_last_text=c["recent"][-1][1] if c["recent"] else "", offpeak=c["offpeak"], now=now)
        print("%-30s p=%.3f %s" % (name, p, "#" * max(1, int(round(p * 40)))))
        print("     理由: %s" % ("、".join(why) if why else "（无信号 → 基础概率）"))


if __name__ == "__main__":
    main()
