"""大肥鱼 · agent 体检报告（只读日志，不发任何消息）。

用法：python3 tools/agent_report.py [小时数]
"""
import json
import re
import subprocess
import sys
import time
from collections import Counter

HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 8
PLUG = "/opt/astrbot/data/plugins/qq_peak_gate"
try:
    out = subprocess.run(["journalctl", "-u", "astrbot", "--since", "-%dh" % HOURS, "--no-pager"],
                         capture_output=True, text=True, timeout=120).stdout
except Exception as e:
    print("读日志失败:", e)
    sys.exit(1)

lines = [l for l in out.splitlines() if "[qq_peak_gate]" in l]
print("# 大肥鱼 · agent 体检报告（最近 %d 小时）\n" % HOURS)

loops = [l for l in lines if "agent loop：开始" in l]
ends = [l for l in lines if "agent loop：结束" in l]
sents = [l for l in lines if "agent：发出" in l]
wakes = [l for l in lines if "定时唤醒" in l]
tool_calls = Counter(re.findall(r"loop: 第 \d+ 轮 ([a-z_]+)\(", "\n".join(lines)))
tool_results = Counter(re.findall(r"agent工具 ([a-z_]+)\(", "\n".join(lines)))
tool_calls.update(tool_results)

tot = cache = miss = 0
for l in ends:
    m = re.search(r"'prompt_tokens': (\d+)", l)
    if m:
        tot += int(m.group(1))
    m = re.search(r"'prompt_cache_hit_tokens': (\d+)", l)
    if m:
        cache += int(m.group(1))
    m = re.search(r"'prompt_cache_miss_tokens': (\d+)", l)
    if m:
        miss += int(m.group(1))
comp = sum(int(m.group(1)) for m in (re.search(r"'completion_tokens': (\d+)", l) for l in ends) if m)
rounds2 = len([l for l in ends if "最多 2 轮" not in l])
finish_n = len([l for l in ends if "finish=True" in l])
texts = re.findall(r"agent：发出「([^」]*)」", "\n".join(sents))
lens = [len(t) for t in texts] or [0]

print("## 1. 规模")
print("- loop 轮次：**%d 次**（%d 条消息触发）" % (len(loops), len(loops)))
print("- 发出消息：**%d 条**，平均 **%.1f 字**，最长 %d 字" % (len(texts), sum(lens) / len(lens), max(lens)))
print("- 她选择沉默（finish）：%d 次" % finish_n)
print("- 定时唤醒：%d 条相关日志" % len(wakes))

print("\n## 2. 成本")
if tot:
    print("- prompt 合计 **%d**，其中**缓存命中 %d（%.0f%%）**，未命中 %d" % (tot, cache, 100.0 * cache / max(1, tot), miss))
    print("- completion 合计 %d" % comp)
    print("- 单条消息平均 prompt %.0f token" % (tot / max(1, len(loops))))
else:
    print("- 没抓到 usage（可能日志已滚动）")

print("\n## 3. 工具使用")
if tool_calls:
    for k, v in tool_calls.most_common(12):
        print("- `%s` × %d" % (k, v))
else:
    print("- 没抓到工具调用（可能这一段没有 loop 或日志级别不够）")

print("\n## 4. 质量/故障")
try:
    f = json.load(open(PLUG + "/data/agent_faults.json", encoding="utf-8"))
    c = f.get("counts") or {}
    print("- 故障计数：%s" % (json.dumps(c, ensure_ascii=False) if c else "无 ✅"))
except Exception:
    print("- 故障计数：无记录 ✅")
dups = [t for t, n in Counter([t for t in texts if len(t) > 3]).items() if n >= 2]
print("- 重复发出过的句子：%d 条" % len(dups))
for t in dups[:6]:
    print("  - 「%s」× %d" % (t[:24], Counter(texts)[t]))
face_n = len([l for l in sents if "表情" in l])
print("- 带表情的发出：%d 条（%.0f%%）" % (face_n, 100.0 * face_n / max(1, len(texts))))

print("\n## 5. 素材")
try:
    idx = json.load(open(PLUG + "/data/stickers/index.json", encoding="utf-8"))
    print("- 表情库：%d 张" % len(idx.get("items") or []))
except Exception:
    pass
try:
    n = 0
    import glob
    for p in glob.glob(PLUG + "/data/agent_memory/*.jsonl"):
        n += len(open(p, encoding="utf-8").readlines())
    print("- 她自己写的记忆：%d 条" % n)
except Exception:
    pass
try:
    w = json.load(open(PLUG + "/data/agent_wake.json", encoding="utf-8")) or []
    print("- 待办定时：%d 条" % len(w))
except Exception:
    pass
print("\n_生成时间：%s_" % time.strftime("%Y-%m-%d %H:%M"))
