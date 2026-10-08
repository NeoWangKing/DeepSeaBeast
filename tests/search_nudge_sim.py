"""离线验证：可查的事实问题要先去查（判定函数 + 提示词 + 兜底在位）。

跑法：python3 tests/search_nudge_sim.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from agent import websearch as W      # noqa: E402
from promptlib import sections as SEC  # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-50s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


print("== 该判定成「可查的事实问题」 ==")
LOOKUPS = ["那终末地下一个版本是什么时候更新",     # 真实案例
           "明日方舟下个活动什么时候开",
           "新版本几号上线？",
           "IEM 科隆谁赢了",
           "下周赛程打谁",
           "这皮肤多少钱",
           "终末地什么时候公测呢",
           "最新公告出了吗",
           "复刻卡池是哪天"]
for t in LOOKUPS:
    ck("查 %s" % t[:24], W.is_lookup_question(t))

print("== 不该判定成事实问题（聊天/观点/玩笑） ==")
NOT_LOOKUPS = ["哈哈哈哈笑死", "我在摸鱼", "你说得对", "这图什么意思", "我觉得挺好玩的",
               "今天好累啊", "凯尔希没了永生这事我一直没缓过来", "", "晚点再聊"]
for t in NOT_LOOKUPS:
    ck("不查 %s" % (t[:24] or "（空串）"), not W.is_lookup_question(t))
ck("超长文本不误判", not W.is_lookup_question("什么时候更新" * 30))

print("== 「不知道」式回答识别 ==")
for t in ["不知道，我又不是鹰角内部人员", "不清楚", "没听说啊", "我哪知道", "等官方吧",
          "我搜了下没找到", "我搜了下没找到，这会儿网也断着", "搜不到", "没查到"]:
    ck("装傻 %s" % t[:20], W.is_dontknow(t))
for t in ["我觉得下个月吧", "查了，大概是 11 月，官方公告写的", "哈哈", ""]:
    ck("不算装傻 %s" % (t[:20] or "（空串）"), not W.is_dontknow(t))

print("== 提示词硬规则 ==")
txt = SEC._search_rules({"caps": {"search": True}})
ck("开了搜索才注入", txt.startswith("【没把握就查"))
ck("写明「不知道」不算回答", "不算回答" in txt and "我又不是内部人员" in txt)
ck("写明查不到就直说", "我搜了下没找到" in txt)
ck("关掉搜索就不注入", SEC._search_rules({"caps": {"search": False}}) == "")

print("== 要不要查：规则 → 小模型判一次 → 本地证据够就不强制 ==")
_calls = []


def _stub_judge(msgs):
    _calls.append(msgs[-1]["content"])
    return "1" if "终末地" in msgs[-1]["content"] else "0"


ck("强规则命中直接 yes（不花钱）",
   W.needs_lookup("终末地什么时候更新", _stub_judge) == "yes" and not _calls)
ck("不像问句 → 空（不花钱）",
   W.needs_lookup("哈哈哈哈笑死", _stub_judge) == "" and not _calls)
ck("像问句 → 小模型判 yes", W.needs_lookup("终末地值得回坑吗", _stub_judge) == "yes")
ck("小模型这次真的被调用", bool(_calls))
_n1 = len(_calls)
ck("第二次同样的问题走缓存（不再调用）", W.needs_lookup("终末地值得回坑吗", _stub_judge) == "yes"
   and len(_calls) == _n1)
ck("判 no 的情况", W.needs_lookup("你在干嘛呢", _stub_judge) == "no")
ck("判定炸了也不报错", W.needs_lookup("这个问题怎么样", lambda m: (_ for _ in ()).throw(ValueError())) == "")
ck("chat_fn 缺失 → 空（交给她自己判断）", W.needs_lookup("这个问题怎么样", None) == "")

ck("should_force: yes+没证据 → 强制", W.should_force("yes", 0.0, 0.34))
ck("should_force: yes+资料够硬 → 不强制", not W.should_force("yes", 0.5, 0.34))
ck("should_force: 边界（等于阈值）不强制", not W.should_force("yes", 0.34, 0.34))
ck("should_force: no/空 → 不强制", not W.should_force("no", 0.0) and not W.should_force("", 0.0))

print("== 查证结论写回记忆 ==")
_note = W.remember_note("终末地下个版本什么时候更新", "查了下，官方公告写的是 10 月 9 日更新")
ck("正常一问一答生成条目", _note.get("kind") == "topic" and "10 月 9 日" in _note.get("text", ""))
ck("问题太长不记", W.remember_note("问" * 80, "答") == {})
ck("答案太长不记", W.remember_note("问", "答" * 200) == {})
ck("空的不记", W.remember_note("", "答案") == {} and W.remember_note("问", "") == {})

print("== 「我再看/我再查」不能变成悬空承诺 ==")
for t in ["HLTV 上写的，具体几点没标，我再看看是哪个赛事", "等下我查查", "稍等，我确认一下",
          "回头再告诉你", "让我确认下",
          "我这就去看看，等我一下",                    # 真实案例
          "想看的话我晚上帮你瞅瞅具体对阵",              # 真实案例
          "等我一下", "我去瞅瞅", "我查查啊", "马上回来"]:
    ck("认得出尾巴 %s" % t[:20], W.is_dangling_promise(t))
for t in ["查到了，明天 10 月 9 日打 Falcons", "我搜了下没找到", "哈哈哈哈", "",
           "你去看看这个", "看完了", "我看完了", "这周末有比赛"]:
    ck("不误判 %s" % (t[:20] or "（空串）"), not W.is_dangling_promise(t))
_src_m = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("收尾会补一轮", "她留了「还要再看」的尾巴" in _src_m)
ck("连发提醒不再一味劝收工", "别留一句「我再看看/我再查查」就收工" in
   open(os.path.join(ROOT, "agent", "loop.py"), encoding="utf-8").read())

print("== 承诺的唤醒延迟 ==")
import importlib.util as _iu  # noqa: E402


class _Stub:  # 借用 main.py 里的静态方法（不实例化插件）
    pass


_src_m = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("有延迟函数（晚上→1小时）", "_promise_delay" in _src_m and '"晚上"' in _src_m)
ck("被动路径也检测承诺（挂在 smart_quote 前）", "_note_promise(event)" in _src_m)
ck("唤醒延迟用 _promise_delay", "_promise_delay(txt)" in _src_m)

print("== 时间锚点 + 「下一场」规则 ==")
_nt = W.now_text()
ck("now_text 形如 YYYY-MM-DD HH:MM 周X", len(_nt.split()) == 3 and _nt[:4].isdigit() and "周" in _nt, _nt)
_rt = SEC._search_rules({"caps": {"search": True}})
ck("提示词要求只给今天之后的信息", "今天之后" in _rt and "下一场" in _rt)
ck("明确禁止拿打完的比赛当答案", "已经打完的比赛" in _rt)
check_src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("main 注入了【现在】", "【现在】" in check_src and "now_text()" in check_src)

print("== 第一轮强制先查（结构约束） ==")
from agent import loop as L       # noqa: E402
from agent import tools as TOOLS  # noqa: E402


class _Stub(TOOLS.Tools):
    pass


seen = []


def _fake_chat(msgs, schema, tc=None):
    seen.append(tc)
    return {"content": "", "tool_calls": [{"id": "1", "name": "web_search",
                                           "arguments": {"query": "x"}}], "usage": {}}


t = TOOLS.Tools("g", {"web_search": lambda q, n=5: "搜到 1 条"}, {})
L.run(t, [{"role": "user", "content": "hi"}], [{"type": "function", "function": {"name": "web_search"}}],
      _fake_chat, 1, lambda *a: None, None, "web_search")
ck("第一轮被强制成 web_search", seen and seen[0] == {"type": "function", "function": {"name": "web_search"}},
   str(seen[0] if seen else None))
seen2 = []


def _fake_chat2(msgs, schema, tc=None):
    seen2.append(tc)
    return {"content": "", "tool_calls": [{"id": "1", "name": "web_search",
                                           "arguments": {"query": "x"}}], "usage": {}}


t2 = TOOLS.Tools("g", {"web_search": lambda q, n=5: "ok"}, {})
L.run(t2, [{"role": "user", "content": "hi"}], [{"type": "function", "function": {"name": "web_search"}}],
      _fake_chat2, 1, lambda *a: None, None)
ck("不强制时 tool_choice 为空（＝required）", seen2 and seen2[0] is None, str(seen2[0] if seen2 else None))
seen3 = []


def _fake_chat3(msgs, schema):
    seen3.append("2参桩")
    return {"content": "", "tool_calls": [], "usage": {}}


L.run(TOOLS.Tools("g", {}, {}), [{"role": "user", "content": "hi"}],
      [{"type": "function", "function": {"name": "web_search"}}], _fake_chat3, 1, lambda *a: None, None, "web_search")
ck("老的两参数桩还能用", seen3 == ["2参桩"])

print("== main.py 里的两层兜底在位 ==")
src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("当轮提醒可查问题", "【这是可查的事实问题】" in src)
ck("答不知道且没查 → 补一轮", "查证兜底：可查的问题没查就答" in src)
ck("补轮提示词", "先调 web_search 查一下，再补一句自然的回复" in src)
ck("故障计数 no_search_answer", '_fault("no_search_answer"' in src)
ck("工具调用打到 INFO（可审计）", "agent loop：本轮调用 → " in src)
ck("提示词禁止编造搜索过程", "没调 web_search 就不能说" in SEC._search_rules({"caps": {"search": True}}))
ck("可查问题会在 main 里强制先查", "_force = \"web_search\"" in src)
ck("提示词给了照做的例子", "照这个例子做" in SEC._search_rules({"caps": {"search": True}}))
ck("main 里有小模型判定器", "def _judge_fn(" in src)
ck("main 里本地证据够就不强制", "should_force(_dec, _ev, _th)" in src)
ck("main 里查证写记忆", "查证记忆：已记下" in src)

print()
if FAIL:
    print("有问题：%s" % "、".join(FAIL))
    sys.exit(1)
print("全部通过")
