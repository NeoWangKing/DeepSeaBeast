#!/usr/bin/env python3
"""离线联调：用桩模块顶掉 AstrBot，直接跑 qq_peak_gate 的真实 gate() 逻辑。
不联网、不调模型、不用 QQ —— 用来验证"要不要回"的决策，改完插件先跑这个。
用法：python3 gate_sim.py
"""
import asyncio
import copy
import importlib.util
import os
import random
import sys
import time as _time
import types

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT = HERE
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)
random.seed(20260929)
# 固定"模拟时钟"起点：2026-09-29 21:00（白天/晚上，避开深夜减分带来的干扰）
T0 = _time.mktime(_time.strptime("2026-09-29 21:00", "%Y-%m-%d %H:%M"))


# ---------------- 桩：astrbot ----------------
class _Logger:
    def info(self, *a):
        pass

    def debug(self, *a):
        pass

    def warning(self, *a):
        pass

    def error(self, *a):
        pass


class _Filter:
    class EventMessageType:
        GROUP_MESSAGE = "group"
        FRIEND_MESSAGE = "friend"

    def event_message_type(self, *a, **k):
        return lambda f: f

    def on_decorating_result(self, *a, **k):
        return lambda f: f

    def on_llm_request(self, *a, **k):
        return lambda f: f

    def on_waiting_llm_request(self, *a, **k):
        return lambda f: f


class _Star:
    def __init__(self, context=None, config=None):
        self.context = context
        self.logger = _Logger()


class _At:
    def __init__(self, qq):
        self.qq = qq


def _install_stubs():
    at = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    comps = types.ModuleType("astrbot.api.message_components")
    star = types.ModuleType("astrbot.api.star")
    class _MessageChain:
        def __init__(self, chain=None, **kw):
            self.chain = list(chain or [])

    event.AstrMessageEvent = object
    event.MessageChain = _MessageChain
    event.filter = _Filter()
    class _Plain:
        def __init__(self, text=""):
            self.text = text

    class _Kw:                                   # 支持带关键字参数的组件（如 Reply(id=...)）
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class Image(_Kw):      # 名字必须与真实组件一致（测试按类名断言）
        pass

    class Reply(_Kw):
        pass

    comps.At, comps.Image, comps.Plain, comps.Reply = _At, Image, _Plain, Reply
    star.Context, star.Star, star.register = object, _Star, (lambda *a, **k: (lambda c: c))
    at.api, api.event, api.message_components, api.star = api, event, comps, star
    sys.modules.update({"astrbot": at, "astrbot.api": api, "astrbot.api.event": event,
                        "astrbot.api.message_components": comps, "astrbot.api.star": star})


_install_stubs()
import scoring  # noqa: E402
spec = importlib.util.spec_from_file_location("qqg_main", os.path.join(HERE, "main.py"))
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


class FakeMsg:
    def __init__(self, mid=1, message=None):
        self.message_id = mid
        self.message = list(message or [])


class FakeEvent:
    """够用的 AstrMessageEvent 替身。"""

    def __init__(self, text, uid="u1", name="同学A", gid="999999111", mid=1, ats=()):
        self.message_str = text
        self._uid, self._name, self._gid, self._mid = uid, name, gid, mid
        self.message_obj = FakeMsg(mid, [_At(q) for q in ats])
        self.unified_msg_origin = "qq:%s:%s" % (gid, uid)
        self.is_wake = False
        self.is_at_or_wake_command = False
        self.stopped = False
        self.sent = []
        self.bot = self

    def get_group_id(self):
        return self._gid

    def get_sender_id(self):
        return self._uid

    def get_sender_name(self):
        return self._name

    def get_self_id(self):
        return "3237702352"

    def get_messages(self):
        return self.message_obj.message

    def stop_event(self):
        self.stopped = True

    async def send(self, chain=None):
        texts, kinds = [], []
        for x in (getattr(chain, "chain", None) or []):
            kinds.append(type(x).__name__)
            t = getattr(x, "text", None)
            if t:
                texts.append(t)
        self.sent.append("".join(texts) or str(chain))
        self.sent_kinds = getattr(self, "sent_kinds", [])
        self.sent_kinds.append(tuple(kinds))

    def get_result(self):
        return types.SimpleNamespace(chain=[])

    async def call_action(self, action, **kw):
        if action == "get_login_info":
            return {"nickname": "大肥鱼-哈"}
        return {}


class Clock:
    """可控时钟：只替换插件模块里用到的 time.time / time.localtime。"""

    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now

    def localtime(self, t=None):
        return _time.localtime(self.now if t is None else t)

    def strftime(self, fmt, t=None):
        return _time.strftime(fmt, self.localtime(t))

    def gmtime(self, t=None):
        return _time.gmtime(self.now if t is None else t)


    def advance(self, sec):
        self.now += sec


def _clean_test_sessions():
    """清掉测试群可能残留的海龟汤会话，避免污染后续场景。"""
    import glob as _gl, os as _os
    d = _os.path.join(HERE, "data", "games", "turtle_soup")
    for f in _gl.glob(_os.path.join(d, "*.json")):
        base = _os.path.basename(f)
        if base.split(".")[0] in ("777", "778", "888", "999", "AAA", "g26",
                                  "qq_777_u1", "qq_888_u1"):
            try:
                _os.remove(f)
            except Exception:
                pass


def new_plugin(cfg_over=None, clock=None):
    cfg = copy.deepcopy(main.DEFAULTS)   # 深拷贝：避免场景之间相互污染
    cfg.update({"log_chat": False, "max_auto_per_hour": 6, "min_interval_sec": 180,
                "engaged_interval_sec": 2, "engaged_window_sec": 90,
                "max_engaged_streak": 6, "engaged_rest_sec": 60,
                "burst_suppress_sec": 30, "human_delay": {"enabled": False}, "force_mode": "offpeak"})
    cfg.update(cfg_over or {})
    main.load_config = lambda: dict(cfg)
    if clock:
        main.time = clock
        scoring.time = clock
    p = main.QqPeakGate.__new__(main.QqPeakGate)
    main.QqPeakGate.__init__(p, None, None)
    p.cfg = dict(cfg)
    p.scorer.cfg = p.cfg
    return p


async def send(plugin, ev):
    await plugin.gate(ev)
    return not ev.stopped


async def scenario(name, steps, cfg_over=None, clock=None, expect=None):
    p = new_plugin(cfg_over, clock)
    clock = clock or Clock(T0)
    replied, detail = 0, []
    for i, (dt, text, kw) in enumerate(steps):
        clock.advance(dt)
        ev = FakeEvent(text, **kw)
        ok = await send(p, ev)
        if ok:
            replied += 1
            detail.append(text[:14])
            # 模拟她真的回了 → 记录"她最后一次开口"
            p.last_reply_ts[str(ev.get_group_id())] = clock.time()
            p.last_auto[str(ev.get_group_id())] = clock.time()
            p.last_reply_to[str(ev.get_group_id())] = str(ev.get_sender_id())
    tag = ""
    if expect is not None:
        tag = " ✅" if expect(replied, len(steps)) else " ❌ 不符合预期"
    print("%-34s 回 %2d / %2d 条%s" % (name, replied, len(steps), tag))
    if detail:
        print("      回了：%s" % "、".join(detail[:8]))
    return replied, len(steps)


async def main_():
    print("=== 场景验证（全部离线，不花钱）===\n")
    _clean_test_sessions()

    # 1) 安静群，200 条互不相干的闲聊
    steps = [(30, "今天天气还行", {"uid": "u%d" % (i % 5)}) for i in range(200)]
    await scenario("1 安静群 200 条闲聊(约1.7h)", steps,
                   expect=lambda r, n: 1 <= r <= 12)

    # 2) 核心诉求验证：控制变量对比同一个句子的 p
    #    A 她刚说完 + 刚才那个人接着问（真·对话回合）
    #    B 她刚说完 + 别人插一句同样的话
    #    C 群里自己在聊同样的话（她没参与）
    s0 = scoring.Scorer(HERE, {})
    now0 = T0
    txt = "那你觉得去哪个好？"
    common = dict(text=txt, sender_id="u1", msg_times=[now0 - 20, now0 - 40],
                  sender_times=[(now0 - 20, "u1")], offpeak=True, now=now0)
    pA, _ = s0.probability(recent=[("我", "我觉得都行啊")], last_reply_ts=now0 - 20,
                           last_reply_to="u1", **common)
    pB, _ = s0.probability(recent=[("我", "我觉得都行啊")], last_reply_ts=now0 - 20,
                           last_reply_to="u2", **common)
    pC, _ = s0.probability(recent=[("u2", "我觉得都行啊")], last_reply_ts=0,
                           last_reply_to="", **common)
    ok = pA >= 1.8 * pC and pA > pB > pC
    print("%-34s A接她话=%.3f  B别人接=%.3f  C群友自己聊=%.3f → %s"
          % ("2 打分口径（同句控制变量）", pA, pB, pC,
             "✅ 接话概率 %.1f 倍于普通消息" % (pA / max(1e-6, pC)) if ok else "❌ 区分度不够"))

    # 2b) 端到端：她在对话里"有机会时"确实会开口
    p2 = new_plugin({"force_mode": "offpeak", "max_auto_per_hour": 999})
    clk2 = Clock(T0)
    main.time = scoring.time = clk2
    FOLLOW2 = ["那你觉得去哪个好？", "那你说吃啥呢", "真的假的啊", "要不你来定吧"]
    chance = rep = 0
    for i in range(20):
        clk2.advance(30)
        ev = FakeEvent("小鲸鱼在吗", uid="u1")
        if await send(p2, ev):
            g = str(ev.get_group_id())
            p2.last_reply_ts[g] = p2.last_auto[g] = clk2.time(); p2.last_reply_to[g] = "u1"
        clk2.advance(20)
        ev = FakeEvent(FOLLOW2[i % 4], uid="u1")
        before = p2.engaged_streak.get(str(ev.get_group_id()), (0, 0))[0]
        if await send(p2, ev):
            g = str(ev.get_group_id())
            p2.last_reply_ts[g] = p2.last_auto[g] = clk2.time(); p2.last_reply_to[g] = "u1"
            rep += 1
        chance += 1
    print("%-34s %d 次接话机会里回了 %d 次 → %s"
          % ("2b 端到端（对话中开口率）", chance, rep,
             "✅ 会接话" if rep >= chance * 0.5 else "❌ 不合预期"))

    # 3) 高峰时段：普通消息一律不回，@ 和叫名字才回
    steps = [(30, "有人吗", {"uid": "u2"}), (30, "谁去拿外卖", {"uid": "u3"}),
             (30, "大肥鱼在吗", {"uid": "u2"}), (30, "谁去拿外卖", {"uid": "u3"}),
             (30, "问一下你们", {"uid": "u2", "ats": ["3237702352"]})]
    await scenario("3 高峰时段门控", steps, cfg_over={"force_mode": "peak"},
                   expect=lambda r, n: r == 2)

    # 4) 一个人在自言自语刷屏
    steps = [(5, "啊啊啊不想写作业", {"uid": "u9"}) for _ in range(20)]
    await scenario("4 单人刷屏 20 条", steps,
                   expect=lambda r, n: r <= 1)

    # 5) 每小时额度用满
    steps = [(200, "问一下这个咋弄？", {"uid": "u%d" % i}) for i in range(12)]
    r, n = await scenario("5 每小时 6 条上限", steps, cfg_over={"force_mode": "offpeak"},
                          expect=lambda r, n: r <= 6)

    # 6) 只 @ 别人，不是在跟她说话
    steps = [(200, "小明你明天来不来", {"uid": "u4", "ats": ["10001"]}) for _ in range(20)]
    await scenario("6 一直在 @ 别人", steps, expect=lambda r, n: r / n <= 0.12)

    # 7) 引用她的消息说话（应必回）
    steps = [(200, "这条你咋看？", {"uid": "u5"}) for _ in range(5)]
    p = new_plugin({"force_mode": "offpeak"}, Clock(T0))
    got = 0
    for dt, text, kw in steps:
        main.time.advance(dt)
        ev = FakeEvent(text, **kw)
        ev.is_at_or_wake_command = True      # 模拟"被引用"
        got += 1 if await send(p, ev) else 0
    print("%-34s 回 %2d / %2d 条%s" % ("7 被引用说话（应全回）", got, len(steps),
                                       " ✅" if got == len(steps) else " ❌"))

    # 9) 分群人格：闪电群用毒舌版，别的群用温和版
    p9 = new_plugin({"force_mode": "offpeak",
                     "prompt_by_group": {"869622030": "system_prompt_sharp.txt"}})
    def mkreq():
        return types.SimpleNamespace(contexts=[], prompt="在吗", system_prompt="旧人格",
                                     func_tool=None, extra_user_content_parts=[])
    r1, r2 = mkreq(), mkreq()
    await p9.compact_context(FakeEvent("大肥鱼 在吗", gid="869622030"), r1)
    await p9.compact_context(FakeEvent("大肥鱼 在吗", gid="123456"), r2)
    sharp_ok = "放开玩" in (r1.system_prompt or "")
    mild_ok = ("玩梗要适度" in (r2.system_prompt or "")) and ("放开玩" not in (r2.system_prompt or ""))
    print("%-34s 闪电群=%s 其他群=%s -> %s" % ("9 分群人格（毒舌/温和）",
          "毒舌" if sharp_ok else "?", "温和" if mild_ok else "?",
          "OK" if (sharp_ok and mild_ok) else "FAIL"))

    # 10) 工具模式群：只有被 @ 才说话（叫名字、闲聊一律不回）
    TOOL_GID = "966812151"
    cfg10 = {"force_mode": "offpeak",
             "only_at_groups": [TOOL_GID],
             "no_memes_groups": [TOOL_GID],
             "prompt_by_group": {TOOL_GID: "system_prompt_tool.txt"}}
    p10 = new_plugin(cfg10)
    clk10 = Clock(T0); main.time = scoring.time = clk10
    r_name = await send(p10, FakeEvent("大肥鱼 在吗", uid="u1", gid=TOOL_GID))
    r_chat = await send(p10, FakeEvent("今天天气不错啊", uid="u2", gid=TOOL_GID))
    clk10.advance(30)
    r_at = await send(p10, FakeEvent("帮我看下这个问题", uid="u3", gid=TOOL_GID,
                                     ats=["3237702352"]))
    ok10 = (not r_name) and (not r_chat) and r_at
    print("%-34s 叫名字=%s 闲聊=%s 被@=%s -> %s" % ("10 工具模式群（只认 @）",
          "回" if r_name else "不回", "回" if r_chat else "不回", "回" if r_at else "不回",
          "OK" if ok10 else "FAIL"))

    # 10b) 工具模式群不注入梗库，且用工具人格
    req10 = types.SimpleNamespace(contexts=[], prompt="帮我看下这个问题", system_prompt="旧",
                                  func_tool=None, extra_user_content_parts=[])
    await p10.compact_context(FakeEvent("帮我看下这个问题", uid="u3", gid=TOOL_GID,
                                        ats=["3237702352"]), req10)
    tool_ok = ("机器人助手" in (req10.system_prompt or "")) and ("梗库速查" not in (req10.system_prompt or ""))
    print("%-34s 人格=工具版 梗库=%s -> %s" % ("10b 工具群人格与梗库",
          "已注入(错)" if "梗库速查" in (req10.system_prompt or "") else "未注入",
          "OK" if tool_ok else "FAIL"))

    # 11) 隐私群：不读上下文、不留语料
    import shutil
    logdir = "/tmp/qqg_test_chatlog"
    shutil.rmtree(logdir, ignore_errors=True)
    cfg11 = {"force_mode": "offpeak", "log_chat": True, "log_dir": logdir,
             "only_at_groups": [TOOL_GID], "no_context_groups": [TOOL_GID],
             "no_log_groups": [TOOL_GID],
             "prompt_by_group": {TOOL_GID: "system_prompt_tool.txt"}}
    p11 = new_plugin(cfg11)
    clk11 = Clock(T0); main.time = scoring.time = clk11
    for t in ["有人在群里说了一句话", "另一个人接着说"]:
        await send(p11, FakeEvent(t, uid="u1", gid=TOOL_GID))
        clk11.advance(10)
    ev11 = FakeEvent("帮我算个数", uid="u3", gid=TOOL_GID, ats=["3237702352"])
    req11 = types.SimpleNamespace(contexts=[], prompt="帮我算个数", system_prompt="旧",
                                  func_tool=None, extra_user_content_parts=[])
    await p11.compact_context(ev11, req11)
    no_digest = "[最近群聊" not in (req11.prompt or "")
    p11._log_chat(ev11, "帮我算个数")
    p11._log_chat(FakeEvent("别的群的消息", uid="u9", gid="123456"), "别的群的消息")
    import os as _os
    files = _os.listdir(logdir) if _os.path.isdir(logdir) else []
    body = ""
    for f in files:
        body += open(_os.path.join(logdir, f), encoding="utf-8").read()
    no_leak = (TOOL_GID not in body) and ("123456" in body)
    print("%-34s 摘要=%s 语料=%s -> %s" % ("11 隐私群（不读不留）",
          "没有" if no_digest else "还在(错)",
          "只留别的群" if no_leak else "泄露了(错)",
          "OK" if (no_digest and no_leak) else "FAIL"))

    # 12) 私聊：用温柔版人格，且保留上下文（不压缩）
    p12 = new_plugin({"force_mode": "offpeak",
                      "prompt_by_group": {"private": "system_prompt_private.txt"}})
    req12 = types.SimpleNamespace(contexts=[{"role": "user", "content": "上次说的那个事"}],
                                  prompt="在吗", system_prompt="旧人格",
                                  func_tool=types.SimpleNamespace(tools=[1, 2, 3]),
                                  extra_user_content_parts=[])
    ev12 = FakeEvent("在吗", uid="u1", gid="")
    await p12.compact_context(ev12, req12)
    priv_ok = ("温柔" in (req12.system_prompt or "")) and len(req12.contexts) == 1 \
        and len(req12.func_tool.tools) == 0          # 私聊也摘掉工具 schema（省 token）
    print("%-34s 人格=%s 历史=%d条 工具=%d个 -> %s" % ("12 私聊（温柔版+保留历史）",
          "温柔版" if "温柔" in (req12.system_prompt or "") else "没换(错)",
          len(req12.contexts), len(req12.func_tool.tools), "OK" if priv_ok else "FAIL"))

    # 13) 引用她的消息说话 → 必回档（0.95）
    s13 = scoring.Scorer(HERE, {})
    now13 = T0
    p_quote, w_quote = s13.probability(text="这条你咋看", recent=[("我", "行行行你说了算")],
                                       msg_times=[now13 - 30], last_reply_ts=now13 - 240,
                                       last_reply_to="", sender_id="u9", offpeak=True,
                                       now=now13, quotes_me=True)
    p_plain, _ = s13.probability(text="这条你咋看", recent=[("我", "行行行你说了算")],
                                 msg_times=[now13 - 30], last_reply_ts=now13 - 240,
                                 last_reply_to="", sender_id="u9", offpeak=True, now=now13)
    ok13 = p_quote >= 0.9 and p_quote > p_plain * 2
    print("%-34s 引用她=%.2f vs 不引用=%.2f -> %s" % ("13 引用她的消息→必回", p_quote, p_plain,
                                                     "OK" if ok13 else "FAIL"))

    # 14) 长期记忆注入：白名单群注入群印象+当前说话人档案，白名单外不注入
    MEMG = "869622030"
    p14 = new_plugin({"force_mode": "offpeak",
                      "memory": {"enabled": True, "groups": [MEMG],
                                 "inject": {"group_profile": True, "members": True, "max_members": 2}}})
    def mkreq14():
        return types.SimpleNamespace(contexts=[], prompt="在吗", system_prompt="人格",
                                     func_tool=None, extra_user_content_parts=[])
    r_in = mkreq14()
    await p14.compact_context(FakeEvent("在吗", uid="u1", gid=MEMG), r_in)
    r_out = mkreq14()
    await p14.compact_context(FakeEvent("在吗", uid="u1", gid="123456"), r_out)
    ok14 = ("本群长期印象" in r_in.system_prompt) and ("张三" in r_in.system_prompt) \
        and ("李四" not in r_in.system_prompt) \
        and ("本群长期印象" not in r_out.system_prompt)
    print("%-34s 白名单内=%s 白名单外=%s -> %s" % ("14 长期记忆注入", 
          "注入了" if "本群长期印象" in r_in.system_prompt else "没注入(错)",
          "没注入" if "本群长期印象" not in r_out.system_prompt else "也注入了(错)",
          "OK" if ok14 else "FAIL"))

    # 15) 点名提到的人：她的档案也要带上（"你觉得张三怎么样"）
    r15 = types.SimpleNamespace(contexts=[], prompt="你觉得张三这个人怎么样", system_prompt="人格",
                                func_tool=None, extra_user_content_parts=[])
    await p14.compact_context(FakeEvent("你觉得张三这个人怎么样", uid="u9", gid=MEMG), r15)
    ok15 = ("张三" in r15.system_prompt) and ("李四" not in r15.system_prompt)
    print("%-34s 提到的人被注入=%s，没提的不注入=%s -> %s" % ("15 点名提到的人",
          "是" if "张三" in r15.system_prompt else "否(错)",
          "是" if "李四" not in r15.system_prompt else "否(错)", "OK" if ok15 else "FAIL"))

    # 16) 全量模式：配置 full_groups 的群里，所有人都被注入
    p16 = new_plugin({"force_mode": "offpeak",
                      "memory": {"enabled": True, "groups": [MEMG],
                                 "inject": {"group_profile": True, "members": True,
                                            "max_members": 1, "full_groups": [MEMG],
                                            "full_max_chars": 1200}}})
    r16 = types.SimpleNamespace(contexts=[], prompt="随便聊", system_prompt="人格",
                                func_tool=None, extra_user_content_parts=[])
    await p16.compact_context(FakeEvent("随便聊", uid="u9", gid=MEMG), r16)
    ok16 = ("张三" in r16.system_prompt) and ("李四" in r16.system_prompt)
    print("%-34s 群里所有人都注入=%s -> %s" % ("16 全量注入模式",
          "是" if ok16 else "否(错)", "OK" if ok16 else "FAIL"))

    # 17) 高峰豁免：她刚说完话、别的人接着回应她 → 不该被高峰硬拦
    #     （注意：同一个人紧接着自己连发的那些会被"连发只回一条"规则挡住，所以这里用别人来测）
    p17 = new_plugin({"force_mode": "peak", "max_auto_per_hour": 20})
    clk17 = Clock(T0); main.time = scoring.time = clk17
    r17a = await send(p17, FakeEvent("在吗", uid="u1", gid="777", ats=["3237702352"]))   # 被@ → 必回
    if r17a:
        p17.last_reply_ts["777"] = clk17.time(); p17.last_reply_to["777"] = "u1"
    clk17.advance(20)
    r17_stranger = await send(p17, FakeEvent("今天天气不错啊", uid="u9", gid="777"))      # 无关的人 → 高峰该拦
    clk17.advance(5)
    r17b = await send(p17, FakeEvent("对", uid="u1", gid="777"))                        # 链条被 u9 打断 → 应放行
    ok17 = r17a and r17b and (not r17_stranger)
    print("%-34s 被@=%s 无关者=%s 回过的人接话=%s -> %s" % ("17 高峰豁免",
          "回" if r17a else "不回", "回" if r17_stranger else "不回",
          "回" if r17b else "不回(错)", "OK" if ok17 else "FAIL"))

    # 18) 同一人连发：只回一条；且第一条不会因为"刷屏检测"被判死
    p18 = new_plugin({"force_mode": "offpeak", "max_auto_per_hour": 20})
    clk18 = Clock(T0); main.time = scoring.time = clk18
    r18a = await send(p18, FakeEvent("在吗", uid="u1", gid="888", ats=["3237702352"]))
    if r18a:
        p18.last_reply_ts["888"] = clk18.time(); p18.last_reply_to["888"] = "u1"
    clk18.advance(3)
    await send(p18, FakeEvent("我插一句", uid="u2", gid="888"))      # 别人插话，打断连发链条
    burst_hits = 0
    for t in ("接着说第1句", "接着说第2句", "接着说第3句", "接着说第4句"):
        clk18.advance(3)
        if await send(p18, FakeEvent(t, uid="u1", gid="888")):
            burst_hits += 1
            p18.last_reply_ts["888"] = clk18.time(); p18.last_reply_to["888"] = "u1"
    ok18 = r18a and burst_hits == 1
    print("%-34s 连发4条回了%d条（期望只回1条，且第一条没被刷屏判死）-> %s"
          % ("18 连发只回一条", burst_hits, "OK" if ok18 else "FAIL"))

    # 20) 连发合并：把一句话拆成几条发 → 等他说完，当成一整句回一次
    import asyncio as _aio
    p20 = new_plugin({"force_mode": "offpeak", "max_auto_per_hour": 20,
                      "human_delay": {"enabled": True, "base_sec": 0.3, "chars_per_sec": 100,
                                      "min_sec": 0.3, "max_sec": 0.3, "jitter": 0}})
    clk20 = Clock(T0); main.time = scoring.time = clk20
    t1 = _aio.create_task(send(p20, FakeEvent("大肥鱼我跟你", uid="u1", gid="AAA")))  # 第一句里含"大肥鱼"
    await _aio.sleep(0.05)
    clk20.advance(1)
    t2 = _aio.create_task(send(p20, FakeEvent("说个事啊", uid="u1", gid="AAA")))
    r1, r2 = await t1, await t2
    # 第二条回复时，prompt 里应该把第一条也拼进"当前消息"
    req20 = types.SimpleNamespace(contexts=[], prompt="说个事啊", system_prompt="人格",
                                  func_tool=None, extra_user_content_parts=[])
    await p20.compact_context(FakeEvent("说个事啊", uid="u1", gid="AAA"), req20)
    tailmsg = (req20.prompt or "").split("[当前消息]")[-1]
    merged = ("大肥鱼我跟你" in tailmsg) and ("说个事啊" in tailmsg)
    ok20 = (not r1) and r2 and merged
    print("%-34s 第一条=%s 第二条=%s 合并成一句=%s -> %s"
          % ("20 连发合并（拆句当一句回）", "回" if r1 else "不回(正确)",
             "回" if r2 else "不回(错)", "是" if merged else "否(错)", "OK" if ok20 else "FAIL"))

    # 21) 拆条发送：长回复切成几条；短的/工具群不切
    cases = [
        ("短句不拆", "？", 3),
        ("两句拆两段", "我先看看这个。你等我一下哈", 2),
        ("四句合到上限", "一是这样。二是那样。三是别的。四是还有别的", 3),
        ("没标点按长度切", "这个东西我不太确定你最好还是问一下别人", 2),
    ]
    ok21 = True
    for label, txt, want_max in cases:
        ps = main.QqPeakGate._split_text(txt, 3, 18)
        good = len(ps) <= want_max and "".join(ps).replace(" ", "") == txt.replace(" ", "")
        ok21 = ok21 and good
        print("     %-14s → %d 段：%s %s" % (label, len(ps), " ｜ ".join(x[:14] for x in ps), "✓" if good else "✗"))
    print("%-34s 内容不丢、段数受限 -> %s" % ("21 拆条发送", "OK" if ok21 else "FAIL"))

    # 22) 拟人延迟：随消息长度增长，且有上下限
    p22 = new_plugin({"force_mode": "offpeak", "human_delay": {"enabled": True, "base_sec": 1.2,
                                                              "chars_per_sec": 12, "min_sec": 0.8,
                                                              "max_sec": 6.0, "jitter": 0.2}})
    ds = [p22._reply_delay(t) for t in ("？", "在吗在吗在吗", "这个问题我想让你认真解释一下，说详细点，别糊弄我")]
    ok22 = (ds[0] >= 2.0) and (ds[1] < ds[2]) and all(d <= 9.6 for d in ds)   # 单字消息按设计等更久（怕对方在连发）
    print("%-34s 短%.1fs 中%.1fs 长%.1fs（递增且受限）-> %s"
          % ("22 拟人延迟", ds[0], ds[1], ds[2], "OK" if ok22 else "FAIL"))

    # 23) 逐字模式：群友要求"一个字一个字回"时放开，且有上限
    p23 = new_plugin({"force_mode": "offpeak"})
    parts = main.QqPeakGate._char_parts("海龟汤好玩！", 24)
    big = main.QqPeakGate._char_parts("这" * 50, 24)
    ok23 = (len(parts) == 5) and (len(big) == 24) and ("".join(parts).replace(" ", "") == "海龟汤好玩！")
    print("%-34s 短句=%s（%s）超长=%d段封顶 -> %s" % ("23 逐字模式",
          len(parts), " ｜ ".join(parts), len(big), "OK" if ok23 else "FAIL"))

    # 24) 模型自定义分段标记 |||（兼容全角/空格）
    p24 = new_plugin({"force_mode": "offpeak"})
    a = main.QqPeakGate._split_by_marker("海|||龟|||汤好玩")
    b = main.QqPeakGate._split_by_marker("海 ｜｜ 龟 ｜｜ 汤")
    d = main.QqPeakGate._split_by_marker("鸡|巴")   # 单个竖线不当分隔符
    c = main.QqPeakGate._split_by_marker("不分段的一句话")
    ok24 = (a == ["海", "龟", "汤好玩"]) and (b == ["海", "龟", "汤"]) and (len(c) == 0) and (len(d) == 0)
    print("%-34s 半角=%s 全角=%s 无标记=%s -> %s" % ("24 模型自定义分段", a, b, "无" if not c else c,
                                                     "OK" if ok24 else "FAIL"))

    # 25) 模型用换行表达分段
    lines = main.QqPeakGate._split_by_lines("加\n班\n真\n爽\n（好了）")
    one = main.QqPeakGate._split_by_lines("就一句话")
    ok25 = (len(lines) == 5) and (len(one) == 0)
    print("%-34s %d 行 -> %s" % ("25 换行当分段", len(lines), "OK" if ok25 else "FAIL"))

    # 28) 顺序验证：白名单开启时，新群也要能建档并放行（建档在校验白名单之前）
    p28 = new_plugin({"force_mode": "offpeak", "auto_persona": {
        "enabled": True, "template": "personas/_template.txt",
        "add_allowed": True, "add_memory": True},
        "prompt_by_group": {}, "allowed_groups": ["869622030"],
        "allowed_self_id": "3237702352",
        "memory": {"enabled": True, "groups": [], "inject": {"group_profile": False, "members": False}}})
    import os as _os
    card28 = _os.path.join(HERE, "personas", "566000222.txt")
    if _os.path.exists(card28):
        _os.remove(card28)
    p28._save_cfg = lambda: None
    ev28 = FakeEvent("你们好", uid="u1", gid="566000222", ats=["3237702352"])
    ev28.get_self_id = lambda: "3237702352"
    await p28.gate(ev28)
    made = _os.path.exists(card28)
    allowed = "566000222" in [str(x) for x in p28.cfg.get("allowed_groups", [])]
    passed = not ev28.stopped          # 建档后应被放行（不再被白名单拦）
    print("%-34s 建档=%s 加入白名单=%s 放行=%s -> %s"
          % ("28 新群建档优先于白名单", "有" if made else "无(错)", "是" if allowed else "否(错)",
             "是" if passed else "被拦(错)", "OK" if (made and allowed and passed) else "FAIL"))

    # 29) 海龟汤：开局 → 判题 → 揭晓退出，全程 stop_event（不走 DS）
    p29 = new_plugin({"force_mode": "offpeak",
                      "turtle": {"enabled": True, "groups": ["777"], "at_only": False,
                                 "start_needs_at": True, "start_keywords": ["海龟汤"],
                                 "exit_keywords": ["揭晓"],
                                 "opening": "开局啦\n\n【汤面】{surface}",
                                 "reveal": "【汤底】{bottom}"}})
    main.turtle_judge.answer = lambda pz, q, facts="", history="", cfg=None: ("是\n就是他打嗝", {"prompt_tokens": 300, "completion_tokens": 8})
    main.turtle_judge.compress_facts = lambda h, cfg=None: ["男人在打嗝"]
    ev29a = FakeEvent("海龟汤", uid="u1", gid="777", ats=["3237702352"])
    await p29.gate(ev29a)
    started = bool(ev29a.stopped and any("开局" in x for x in ev29a.sent))
    ev29b = FakeEvent("他是打嗝吗？", uid="u2", gid="777")
    await p29.gate(ev29b)
    answered = bool(ev29b.stopped and any("打嗝" in x for x in ev29b.sent))
    ev29c = FakeEvent("揭晓", uid="u1", gid="777")
    await p29.gate(ev29c)
    revealed = bool(ev29c.stopped and any("汤底" in x for x in ev29c.sent))
    import games.turtle_soup.session as _ts
    cleared = not _ts.load(ev29c.unified_msg_origin)
    ok29 = started and answered and revealed and cleared
    print("%-34s 开局=%s 判题=%s 揭晓=%s 退出清理=%s -> %s"
          % ("29 海龟汤（全程不走 DS）", "✓" if started else "✗", "✓" if answered else "✗",
             "✓" if revealed else "✗", "✓" if cleared else "✗", "OK" if ok29 else "FAIL"))

    # 30) 海龟汤"自己判断是不是提问"：闲聊不吭声，提问照答（先清残留会话）
    try:
        import games.turtle_soup.session as _ts30
        _ts30.end("778")
    except Exception:
        pass
    p30 = new_plugin({"force_mode": "offpeak",
                      "turtle": {"enabled": True, "groups": ["778"], "question_filter": "llm",
                                 "start_needs_at": True, "start_keywords": ["海龟汤"],
                                 "exit_keywords": ["揭晓"], "opening": "开局：{surface}",
                                 "reveal": "汤底：{bottom}"}})
    calls = {"n": 0}
    def _judge(pz, q, facts="", history="", cfg=None):
        calls["n"] += 1
        if "天气" in q or "几点" in q:          # 模型判断：这不是提问
            return "跳过", {"prompt_tokens": 300, "completion_tokens": 5}
        return "是\n就是他打嗝", {"prompt_tokens": 300, "completion_tokens": 8}
    main.turtle_judge.answer = _judge
    ev30a = FakeEvent("海龟汤", uid="u1", gid="778", ats=["3237702352"])
    await p30.gate(ev30a)
    # a) 纯语气词 → 本地预筛，不调用 GLM、不吭声
    before = calls["n"]
    ev30b = FakeEvent("哈哈哈哈", uid="u2", gid="778")
    await p30.gate(ev30b)
    local_skip = (calls["n"] == before) and ev30b.stopped and not ev30b.sent
    # b) 闲聊（过了本地预筛，GLM 判"跳过"）→ 不吭声
    ev30c = FakeEvent("今天天气不错啊", uid="u3", gid="778")
    await p30.gate(ev30c)
    llm_skip = ev30c.stopped and not ev30c.sent
    # c) 真提问 → 正常回答
    ev30d = FakeEvent("他是不是在打嗝？", uid="u4", gid="778", mid=1234567890)   # 长 id → 应带引用
    await p30.gate(ev30d)
    answered = ev30d.stopped and any("打嗝" in x for x in ev30d.sent)
    kinds = getattr(ev30d, "sent_kinds", [])
    quoted = bool(kinds) and any("Reply" in k for k in kinds)
    ok30 = local_skip and llm_skip and answered and quoted
    print("%-34s 语气词静默=%s GLM判跳过=%s 真提问=%s 带引用=%s -> %s"
          % ("30 海龟汤自己判断是否提问", "✓" if local_skip else "✗", "✓" if llm_skip else "✗",
             "✓" if answered else "✗", "✓" if quoted else "✗", "OK" if ok30 else "FAIL"))

    # 31) 豹群式严格模式：只认 @ 提问；一局状态不落盘（隐私）
    import os as _os31
    _os31.makedirs(_os31.path.join(HERE, "data", "turtle"), exist_ok=True)
    sessfile = _os31.path.join(HERE, "data", "turtle", "966812151.json")
    if _os31.path.exists(sessfile):
        _os31.remove(sessfile)
    p31 = new_plugin({"force_mode": "offpeak",
                      "turtle": {"enabled": True, "groups": ["966812151"],
                                 "question_filter": "llm", "start_needs_at": True,
                                 "start_keywords": ["海龟汤"], "exit_keywords": ["揭晓"],
                                 "opening": "开局：{surface}", "reveal": "汤底：{bottom}",
                                 "per_group": {"966812151": {"question_filter": "at", "no_disk": True}}}})
    main.turtle_judge.answer = lambda pz, q, facts="", history="", cfg=None: ("是\n没错", {"prompt_tokens": 300, "completion_tokens": 5})
    ev31a = FakeEvent("海龟汤", uid="u1", gid="966812151", ats=["3237702352"])
    await p31.gate(ev31a)
    started = bool(ev31a.stopped and any("开局" in x for x in ev31a.sent))
    # 不带 @ 的提问 → 严格模式下应不吭声
    ev31b = FakeEvent("他是不是在打嗝", uid="u2", gid="966812151")
    await p31.gate(ev31b)
    no_at_silent = bool(ev31b.stopped and not ev31b.sent)
    # 带 @ 的提问 → 正常回答
    ev31c = FakeEvent("他是不是在打嗝", uid="u3", gid="966812151", ats=["3237702352"])
    await p31.gate(ev31c)
    at_answered = bool(ev31c.stopped and any("是" in x for x in ev31c.sent))
    # 引用她（但没有 @）→ 严格模式下也不算提问，应静默
    ev31d = FakeEvent("他是不是在打嗝", uid="u4", gid="966812151")
    from astrbot.api.message_components import Reply as _R31
    ev31d.message_obj.message = [_R31(id="1", sender_id="3237702352")]
    await p31.gate(ev31d)
    quote_silent = bool(ev31d.stopped and not ev31d.sent)
    # 以「问」开头（但没有 @）→ 同样静默
    ev31e = FakeEvent("问一下他是不是在打嗝", uid="u5", gid="966812151")
    await p31.gate(ev31e)
    ask_silent = bool(ev31e.stopped and not ev31e.sent)
    # 不带 @ 的「揭晓」→ 严格模式下无效（游戏应继续）
    import games.turtle_soup.session as _ts31
    ev31f = FakeEvent("揭晓", uid="u6", gid="966812151")
    await p31.gate(ev31f)
    exit_silent = bool(ev31f.stopped and not ev31f.sent and _ts31.load("966812151", True))
    # 状态不落盘
    no_disk = not _os31.path.exists(sessfile)
    ok31 = (started and no_at_silent and at_answered and quote_silent and ask_silent
            and exit_silent and no_disk)
    print("%-34s 开局=%s 非@静默=%s 引用也不算=%s 「问」开头也不算=%s @才答=%s 不落盘=%s -> %s"
          % ("31 豹群严格模式（只认@）", "✓" if started else "✗",
             "✓" if no_at_silent else "✗", "✓" if quote_silent else "✗",
             "✓" if ask_silent else "✗", "✓" if at_answered else "✗",
             "✓" if no_disk else "✗", "OK" if ok31 else "FAIL"))

    # 8) 图片快速通道：图片组件必须在"下载之前"被摘掉
    p3 = new_plugin({"force_mode": "offpeak", "image_fastpath": True})
    ev = FakeEvent("大肥鱼 看看这个", uid="u1")
    ev.message_obj.message = [types.SimpleNamespace(type="Plain"),
                              types.SimpleNamespace(type="Image")]
    # 用插件真正 import 的组件类型构造，保证 isinstance 判定有效
    from astrbot.api.message_components import Image as _Img, Plain as _Plain
    ev.message_obj.message = [_Plain(), _Img()]
    await p3.image_fastpath(ev)
    left = [type(x).__name__ for x in ev.message_obj.message]
    ok8 = ("Image" not in left) and any(isinstance(x, _Plain) for x in ev.message_obj.message)
    print("%-34s 摘图片后消息链=%s → %s" % ("8 快速通道开启时摘掉图片", left, "✅" if ok8 else "❌"))

    # 8b) 关掉快速通道时应原样不动（可回滚）
    p4 = new_plugin({"force_mode": "offpeak", "image_fastpath": False})
    ev2 = FakeEvent("大肥鱼 看看这个", uid="u1")
    ev2.message_obj.message = [_Plain(), _Img()]
    await p4.image_fastpath(ev2)
    left2 = [type(x).__name__ for x in ev2.message_obj.message]
    print("%-34s 消息链=%s → %s" % ("8b 默认(关)时图片原样保留", left2,
                                    "✅" if "Image" in left2 else "❌"))

    # 8c) 概率分布抽样：看打分器给不同类型消息的 p
    s = scoring.Scorer(HERE, {})
    print("\n=== 打分器抽样（p 值）===")
    now = _time.time()
    for label, kw in [
        ("普通闲聊", dict(text="今天食堂的饭真难吃")),
        ("问句", dict(text="这个到底怎么弄啊")),
        ("纯哈哈哈", dict(text="哈哈哈哈")),
        ("命中黑话", dict(text="这也太抽象了吧")),
    ]:
        ppp, why = s.probability(recent=[("A", "随便聊聊")], msg_times=[now - 30],
                                 sender_id="u1", offpeak=True, now=now, **kw)
        print("  %-8s p=%.3f  %s" % (label, ppp, "、".join(why)))


if __name__ == "__main__":
    asyncio.run(main_())
