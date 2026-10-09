"""离线验证：Web 管理面板的安全性（只监听本机 / 白名单 / 类型校验 / 认证）。

跑法：/opt/astrbot/.local/share/uv/tools/astrbot/bin/python tests/webadmin_sim.py
（会用内存里的临时 HTTP 服务打几个接口，不碰生产面板进程。）
"""
import base64
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

FAIL = []


def ck(name, cond, extra=""):
    print("  %-52s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


import webadmin as W          # noqa: E402

_cfg = W.load_cfg()

print("== 只监听本机（不许开公网） ==")
_unit = open("/etc/systemd/system/qqbot-webadmin.service", encoding="utf-8").read()
ck("服务单元存在且用 astrbot 的 python", "webadmin.py" in _unit and "astrbot" in _unit)
ck("单元没写 0.0.0.0", "0.0.0.0" not in _unit)
_src = open(os.path.join(ROOT, "tools", "webadmin.py"), encoding="utf-8").read()
ck("默认 host = 127.0.0.1", 'default="127.0.0.1"' in _src)
ck("有基本认证（401 分支）", "WWW-Authenticate" in _src and "compare_digest" in _src)

print("== 配置改动白名单 + 类型校验 ==")
ck("不认识的顶层键会被拒", bool(W.validate_patch({"hack_me": 1}, _cfg)))
ck("数字项给字符串会被拒", bool(W.validate_patch({"stickers": {"max_per_hour": "x"}}, _cfg)))
ck("布尔项给字符串会被拒", bool(W.validate_patch({"perception": {"enabled": "yes"}}, _cfg)))
ck("群号非法会被拒", bool(W.validate_patch({"group_tuning": {"abc": {"min_interval_sec": 5}}}, _cfg)))
ck("概率越界会被拒",
   bool(W.validate_patch({"group_tuning": {"869622030": {"reply_prob_mult": 5}}}, _cfg)))
ck("合法改动通过",
   not W.validate_patch({"group_tuning": {"869622030": {"reply_prob_mult": 0.35}},
                         "perception": {"max_chars": 420}}, _cfg))
ck("null = 删除该项（允许）",
   not W.validate_patch({"prompt_by_group": {"999": None}}, _cfg))

print("== 深合并只动给到的键 ==")
_d = {"a": {"x": 1, "y": 2}, "b": [1]}
W.deep_merge(_d, {"a": {"y": 9}})
ck("只改 a.y", _d == {"a": {"x": 1, "y": 9}, "b": [1]}, json.dumps(_d))

print("== 人格卡文件名守卫 ==")
for bad in ("../evil.txt", "a/b.txt", "x.sh", "x.txt.bak"):
    ok, msg = W.save_persona(bad, "hi")
    ck("拒绝 %s" % bad, ok is False, msg)
_ok, _p = W.save_persona("_wa_test_card.txt", "【测试】\n这张是测试卡\n")
ck("合法名字能保存", _ok is True and os.path.isfile(_p), str(_p))
if _ok:
    _txt, _ = W.read_persona("_wa_test_card.txt")
    ck("读回来内容一致", "测试卡" in (_txt or ""))
    os.remove(_p)

print("== HTTP 接口（内存里起一个临时实例） ==")
srv = W.ThreadingHTTPServer(("127.0.0.1", 0), W.H)
W.H.pw = "test-pw-123"
_t = threading.Thread(target=srv.serve_forever, daemon=True)
_t.start()
_port = srv.server_address[1]
_base = "http://127.0.0.1:%d" % _port


def _req(path, body=None, pw="test-pw-123"):
    url = _base + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        r.add_header("Content-Type", "application/json")
    if pw:
        r.add_header("Authorization", "Basic " +
                     base64.b64encode(("x:" + pw).encode()).decode())
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


try:
    _c, _d = _req("/api/ping", pw="")
    ck("没密码 → 401", _c == 401, str(_c))
    _c, _d = _req("/api/ping")
    ck("有密码 → ping ok", _c == 200 and _d.get("ok") is True, str(_d))
    _c, _d = _req("/api/state")
    ck("state 里有 personas/groups/stickers",
       all(k in _d for k in ("personas", "groups", "stickers")), str(list(_d))[:80])
    ck("state 不含 api_key 之类敏感字段", "api_key" not in json.dumps(_d)[:5000].lower()
       or "api_key_source" not in json.dumps(_d))
    _c, _d = _req("/api/persona/name", {"name": "system_prompt_tool.txt", "title": "接口改名"})
    ck("HTTP 改名接口可用", _d.get("ok") is True and
       W.load_names().get("system_prompt_tool.txt") == "接口改名", str(_d)[:60])
    _req("/api/persona/name", {"name": "system_prompt_tool.txt",
                               "title": "工具模式（豹群·只回@）"})
    _c, _d = _req("/api/apply", {"config": {"hack_me": 1}, "reload": False})
    ck("apply 非法键被拒（且没写文件）", _d.get("ok") is False, str(_d)[:80])
    _c, _d = _req("/api/apply", {"config": {"perception": {"max_chars": "abc"}}, "reload": False})
    ck("apply 类型错被拒", _d.get("ok") is False, str(_d)[:80])
finally:
    srv.shutdown()


print("== null 表示删键（点「默认卡」） ==")
_d = {"prompt_by_group": {"111": "a.txt", "222": "b.txt"}}
W._del_paths(_d, {"prompt_by_group": {"111": None}})
ck("null 会把那项删掉", _d == {"prompt_by_group": {"222": "b.txt"}}, json.dumps(_d))
_d2 = {"a": {"b": None, "c": 1}}
W.prune_nulls(_d2)
ck("prune_nulls 递归删空", _d2 == {"a": {"c": 1}}, json.dumps(_d2))


print("== 人格卡显示名 ==")
_names_bak = W.load_names()
try:
    ck("每张卡都带 title（面板用）",
       all(c.get("title") for c in W.list_personas()))
    ck("有卡已经起了名字（不是文件名）",
       any(c.get("title") != c.get("name") for c in W.list_personas()),
       str([c["title"] for c in W.list_personas()][:3]))
    _ok, _t = W.save_name("system_prompt_tool.txt", "改个名试试")
    ck("改名写入 _names.json", _ok and W.load_names().get("system_prompt_tool.txt") == "改个名试试")
    _c = [c for c in W.list_personas() if c["name"] == "system_prompt_tool.txt"][0]
    ck("列表里的 title 跟着变", _c["title"] == "改个名试试" and _c["renamed"] is True)
    W.save_name("system_prompt_tool.txt", "工具模式（豹群·只回@）")
    _ok2, _t2 = W.save_name("system_prompt_tool.txt", "  ")
    ck("清空名称 = 删掉（回退用文件名）",
       _ok2 and "system_prompt_tool.txt" not in W.load_names(), _t2)
    ck("非 .txt 不给改名", W.save_name("_names.json", "x")[0] is False)
finally:
    W._atomic_write(W.NAMES_PATH, json.dumps(_names_bak, ensure_ascii=False, indent=1))
    ck("测试后已还原 _names.json", W.load_names() == _names_bak)


print("== 群名/人数：只读拉群列表（打桩，不碰网络） ==")
_orig_call = W._panel_call
_calls = {"n": 0}


def _fake_call(action, params=None, timeout=6):
    _calls["n"] += 1
    if action == "get_group_list":
        return [{"group_id": 869622030, "group_name": "⚡", "member_count": 16},
                {"group_id": 111222333, "group_name": "测试小群", "member_count": 3}]
    return None


try:
    W._panel_call = _fake_call
    W._GRP_CACHE["ts"] = 0
    _kg = W.known_groups(refresh_sec=600)
    ck("拿到群名/人数", _kg.get("869622030", {}).get("name") == "⚡"
       and _kg.get("869622030", {}).get("members") == 16, str(_kg)[:80])
    _n1 = _calls["n"]
    W.known_groups(refresh_sec=600)
    ck("第二次走缓存（不再请求）", _calls["n"] == _n1, str(_calls))
    _ov = W.groups_overview()
    _g = {x["gid"]: x for x in _ov}
    ck("总览里带 name/members/in_bot",
       all(k in _g["869622030"] for k in ("name", "members", "in_bot")), str(_g["869622030"])[:80])
    ck("她实际在、但没配置的群也会列出来", "111222333" in _g, str(sorted(_g))[:80])
    ck("没配置的群标 in_allowlist=False", _g["111222333"]["in_allowlist"] is False
       if W.load_cfg().get("allowed_groups") else True)
finally:
    W._panel_call = _orig_call
    W._GRP_CACHE["ts"] = 0
    try:
        os.remove(W.KNOWN_GROUPS)
    except Exception:
        pass
    ck("测试后清掉临时 known_groups.json", not os.path.exists(W.KNOWN_GROUPS))

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
