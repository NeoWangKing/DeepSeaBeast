"""输出协议 + 提示词分层的离线自测（不联网、不依赖 astrbot）。

用法：python3 tests/proto_sim.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import promptlib      # noqa: E402
import replyproto     # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    print("  %-46s %s%s" % (name, "OK" if cond else "FAIL", (" " + str(extra)) if extra else ""))
    if not cond:
        FAIL.append(name)


print("== 输出协议 ==")
p = replyproto.parse("在的 ||| 叫我干嘛")
check("||| 分两条", p.parts == ["在的", "叫我干嘛"] and p.source == "marker")
p = replyproto.parse("好\n那我先睡了")
check("换行分条", p.parts == ["好", "那我先睡了"] and p.source == "lines")
p = replyproto.parse("[不说话]")
check("沉默", p.silent and p.parts == [])
p = replyproto.parse("算了 [不说话]")
check("混着写也算沉默", p.silent)
p = replyproto.parse("笑死我了 [表情:s1790876448115]")
check("表情标记被摘掉、参数保留", p.parts == ["笑死我了"] and p.stickers == ["s1790876448115"])
p = replyproto.parse("笑死 [表情:无语]")
check("情绪式表情标记", p.stickers == ["无语"])
p = replyproto.parse("只发一条普通的话")
check("单条", p.parts == ["只发一条普通的话"] and p.source == "single")
p = replyproto.parse("")
check("空输出", not p.silent and p.parts == [] and p.source == "empty")
p = replyproto.parse("[不说话]", silence=False)
check("关掉沉默协议不残留标记", (not p.silent) and p.parts == [])
p = replyproto.parse("a ||| b ||| c ||| d ||| e ||| f", max_parts=3)
check("超上限合并尾段", len(p.parts) == 3 and p.parts[-1] == "c d e f", p.parts)
p = replyproto.parse("喂，你在吗，，")
check("尾部标点清理", p.parts == ["喂，你在吗"], p.parts)
p = replyproto.parse("DeepSeek V3 真的行")
check("英文词内部空格保留", p.parts == ["DeepSeek V3 真的行"], p.parts)

print("== 发送口兜底 sanitize ==")
check("保留 [QQ表情:…]（必须能变真表情）",
      replyproto.sanitize("好[QQ表情:汪汪]的") == "好[QQ表情:汪汪]的")
check("协议行（图/回/收）清掉", replyproto.sanitize("图：星星\n回：好嘞\n收：是") == "")
check("沉默标记清掉", replyproto.sanitize("[不说话]") == "")
check("图片表情标记默认清掉", replyproto.sanitize("笑死[表情:s1]真的") == "笑死真的")
check("保留图片表情标记（兜底路径用）",
      replyproto.sanitize("笑死[表情:s1]真的", strip_stickers=False) == "笑死[表情:s1]真的")
check("||| 默认清掉", replyproto.sanitize("a ||| b") == "a b")
check("||| 保留（分条路径用）", replyproto.sanitize("a ||| b", strip_bar=False) == "a ||| b")
check("正常话原样", replyproto.sanitize("普通一句话") == "普通一句话")
check("全是标记 → 空（就不发）", replyproto.sanitize("图：x\n回：y\n收：否") == "")

print("== 定时意图识别 wants_schedule ==")
for _t, _exp in [("10秒钟之后发一张搞怪的表情", True), ("过一会儿再说", True),
                 ("等我回来再聊", True), ("5分钟后提醒我", True),
                 ("今天天气不错", False), ("你是谁", False)]:
    check("wants_schedule(%r)" % _t[:12], replyproto.wants_schedule(_t) is _exp)

print("== 提示词分层 ==")
cfg_path = os.path.join(ROOT, "config.json")
if os.path.isfile(cfg_path):
    cfg = json.load(open(cfg_path, encoding="utf-8"))
else:
    cfg = {}
txt, meta = promptlib.build_system_prompt(plugin_dir=ROOT, cfg=cfg, chat_key="0", private=False,
                                          caps={"kb": True})
check("拼装出内容", len(txt) > 1500, meta)
check("含身份句", "混在 QQ 群里的普通群友" in txt)
check("含输出协议段", "【输出协议" in txt and "|||" in txt and "[不说话]" in txt)
check("含安全段", "【安全规则" in txt)
check("含表情策略段", "【表情包" in txt)
check("个性卡在前、行为层在后", txt.index("【安全规则") > 0)
check("覆盖文件可整段替换", promptlib.resolve(ROOT, "behavior/README.md").endswith("README.md"))
check("人格卡解析到文件", promptlib.pick_file(ROOT, cfg, "869622030", False)[0] != "")
check("私聊人格卡解析", promptlib.pick_file(ROOT, cfg, "", True)[0] != "")

print()
if FAIL:
    print("失败 %d 项：%s" % (len(FAIL), "、".join(FAIL)))
    sys.exit(1)
print("全部通过")
