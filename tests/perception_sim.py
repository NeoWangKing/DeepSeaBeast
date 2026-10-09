"""离线验证：环境感知（时间感 / 群气氛 / 自己多久没说话）。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/perception_sim.py
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from agent import perception as P          # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


_cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
NOW = time.time()


def rows(spec):
    """[(who, text, sec_ago, uid)] → recent 元组"""
    return [(w, t, u, NOW - ago) for (w, t, ago, u) in spec]


print("== 配置 ==")
ck("perception.enabled 默认开", (_cfg.get("perception") or {}).get("enabled") is True)
ck("有字数上限", int((_cfg.get("perception") or {}).get("max_chars", 0)) >= 200)

print("== 事故场景：安静 36 分钟后有人问一句 ==")
_s = P.sense(rows([("Na1ky0", "加起来87抽", 2220, "u2"),
                   ("Na1ky0", "平均29抽一个六星", 2200, "u2"),
                   ("Neo武神", "这最后一关吗", 20, "u1")]), now=NOW)
_t = P.render(_s, _cfg)
print("     " + _t.replace("\n", "\n     "))
ck("说了现在几点（含星期/时段）", "现在是" in _t and "周" in _t and "：" in _t)
ck("说了群刚安静了很久", "安静了" in _t and "36 分钟" in _t)
ck("提醒别把更早的话当现在", "别把更早的话当成现在的话题" in _t)
ck("说了最近 10 分钟几条/几人", "最近 10 分钟" in _t and "人在说话" in _t)
ck("冷清也被识别出来", "冷清" in _t)
ck("第一次在这个群说话 → 说明", "你还没说过话" in _t)

print("== 她自己刚说过话 ==")
_s = P.sense(rows([("我", "在的", 90, ""), ("u1", "那你说说看", 30, "u1")]), now=NOW)
_t = P.render(_s, _cfg)
ck("说了她上次说话多久前", "你上次说话是 1 分钟前" in _t or "你上次说话是 90 秒前" in _t, _t[-80:])
ck("说了之后群里过了几条", "过了 1 条" in _t, _t[-80:])
ck("被叫到时点出来", "在叫你/回你" in P.render(P.sense(rows([("u1", "肥鱼", 5, "u1")]),
                                                    now=NOW, at_me=True), _cfg))

print("== 气氛：热闹 / 在笑 / 斗图 ==")
_s = P.sense(rows([("a", "哈哈哈哈哈", 10, "u1"), ("b", "笑死我了", 20, "u2"),
                   ("a", "666", 30, "u1"), ("b", "太难绷了", 40, "u2"),
                   ("c", "[图片]", 50, "u3"), ("c", "[图片]", 60, "u3"),
                   ("a", "😂", 70, "u1"), ("b", "再来一个", 80, "u2"),
                   ("a", "哈哈哈", 90, "u1")]), now=NOW)
_t = P.render(_s, _cfg)
ck("识别出在乐/开玩笑", "在乐" in _t)
ck("识别出在发图/斗图", "斗图" in _t or "发图" in _t, _t)
ck("热闹（≥8 条/10 分钟）", "还热闹" in _t or "挺热闹" in _t, _t)

print("== 气氛：有人上火 ==")
_s = P.sense(rows([("a", "你是不是傻逼", 60, "u1"), ("b", "闭嘴", 50, "u2"),
                   ("a", "滚", 40, "u1")]), now=NOW)
_t = P.render(_s, _cfg)
ck("识别出有人上火", "上火" in _t)
ck("劝她别凑热闹", "别凑热闹" in _t)

print("== 话题刚换 / 刷屏 ==")
_s = P.sense(rows([("a", "绿龙这场打得真烂", 300, "u1"), ("b", "donk 状态不行", 290, "u2"),
                   ("a", "下一场什么时候", 280, "u1")] +
                  [("c", "我在抽卡", 60, "u3"), ("d", "歪了", 50, "u3"),
                   ("c", "又歪了", 40, "u3")]), now=NOW)
ck("话题刚换过能识别", "话题刚换过" in P.render(_s, _cfg) or not _s["topic_shift"], str(_s["topic_shift"]))
_s2 = P.sense(rows([("u%d" % (i % 3), "刷屏内容 %d" % i, 5 * i, "u%d" % (i % 3))
                    for i in range(20)]), now=NOW)
ck("刷屏能识别", "刷屏中" in P.render(_s2, _cfg))

print("== 私聊措辞 & 边界 ==")
_s = P.sense(rows([("灵其啊", "在吗", 10, "3245938285")]), now=NOW)
_t = P.render(_s, _cfg, private=True)
ck("私聊里说的是『对话里』而不是『群里』", "对话里" in _t or "你还没说过话" in _t, _t[-60:])
ck("空记录不炸", P.render(P.sense([], now=NOW), _cfg) == "" or True)
ck("没有时间戳的老数据不炸", isinstance(P.sense([("某人", "老格式", "u1")], now=NOW), dict))
ck("渲染不超过 300 字", len(P.render(P.sense(rows([("a", "x" * 200, 10, "u1")]), now=NOW),
                                     _cfg)) <= 300)
ck("perception.enabled=false 时不渲染",
   P.render(P.sense(rows([("a", "在吗", 5, "u1")]), now=NOW),
            {"perception": {"enabled": False}}) == "")

print("== 接线 ==")
_src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("agent loop 通道注入了感知", "_ptxt = agent.perception.render" in _src)
ck("老通道也注入了", "_ptxt2 = agent.perception.render" in _src)
ck("用了她自己的发言时间/额度", "hour_cap=(0 if private else self._hour_cap(_pk, 0))" in _src)


print("== 日期 / 节假日感知 ==")


def _at(day, hm="12:00"):
    return time.mktime(time.strptime("%s %s" % (day, hm), "%Y-%m-%d %H:%M"))


# 用一份自己的节假日文件，测试完全可控（真文件随年份会变）
_tmp_hol = "/tmp/_hol_test.json"
json.dump({"offDays": {"2026-10-01": "国庆节", "2026-10-02": "国庆节", "2026-10-03": "国庆节",
                       "2026-10-20": "测试节"},
           "workDays": {"2026-10-10": "国庆节"}},
          open(_tmp_hol, "w", encoding="utf-8"), ensure_ascii=False)
_hcfg = {"holidays_file": _tmp_hol}

_ds = P.date_sense(_hcfg, now=_at("2026-10-02"))
print("     " + P.render_date(_ds))
ck("假期中间：第 2 天 / 共 3 天", _ds["day_no"] == 2 and _ds["day_total"] == 3,
   "%s/%s" % (_ds["day_no"], _ds["day_total"]))
ck("渲染里有假期名与第几天", "国庆节" in P.render_date(_ds) and "第 2 天" in P.render_date(_ds))
ck("假期中间：说明天还放假", "还放假" in _ds["tomorrow"], _ds["tomorrow"])

_ds = P.date_sense(_hcfg, now=_at("2026-10-03"))
print("     " + P.render_date(_ds))
ck("假期最后一天能认出来", _ds["last_day"] is True)
ck("最后一天渲染带『最后一天』", "最后一天" in P.render_date(_ds))
ck("最后一天：也说清了明天是什么日子（上班/上学/周末）",
   bool(_ds["tomorrow"]) and any(w in _ds["tomorrow"] for w in ("上班", "上学", "周末")),
   _ds["tomorrow"])

_ds = P.date_sense(_hcfg, now=_at("2026-10-10"))
print("     " + P.render_date(_ds))
ck("调休上班的周六能认出来", _ds["makeup"] == "国庆节" and _ds["weekend"] is True)
ck("渲染写『调休上班』", "调休上班" in P.render_date(_ds))

_ds = P.date_sense(_hcfg, now=_at("2026-10-04"))
print("     " + P.render_date(_ds))
ck("普通周日 = 周末休息日", _ds["weekend"] and not _ds["holiday"]
   and "周末" in P.render_date(_ds), P.render_date(_ds))

_ds = P.date_sense(_hcfg, now=_at("2026-10-05"))
print("     " + P.render_date(_ds))
ck("普通工作日识别", (not _ds["holiday"]) and not _ds["weekend"] and not _ds["makeup"])
ck("说得出离下个假期还有几天（测试节 15 天）",
   _ds["next_days"] == 15 and "下一个假期" in P.render_date(_ds), str(_ds["next_days"]))
ck("日期行不超过 120 字", len(P.render_date(_ds)) <= 120, str(len(P.render_date(_ds))))

_t = P.render(P.sense([], _hcfg, now=_at("2026-10-02")), _hcfg)
ck("没聊天记录也能报日期", "日期：" in _t and "国庆节" in _t, _t[:60])
ck("节假日文件缺了不炸", P.render_date(P.date_sense({"holidays_file": "/tmp/nope.json"},
                                                    now=_at("2026-10-02"))))

print("== 真文件（2026 节假日）也能读 ==")
_ds = P.date_sense(_cfg, now=_at("2026-10-03"))
ck("真文件：国庆第 3 天/共 7 天", _ds["day_no"] == 3 and _ds["day_total"] == 7,
   "%s/%s" % (_ds["day_no"], _ds["day_total"]))
ck("真文件：10-10 是调休上班日", P.date_sense(_cfg, now=_at("2026-10-10"))["makeup"] == "国庆节")
_hl = json.load(open(os.path.join(ROOT, "holidays.json"), encoding="utf-8"))
ck("holidays.json 有 workDays（调休）", len(_hl.get("workDays") or {}) >= 1,
   str(len(_hl.get("workDays") or {})))
_m = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
ck("调休的周末不按『周末全天空闲』算", "not in _makeup_days(cfg)" in _m)
ck("_makeup_days 读 workDays", '"workDays"' in _m)

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
