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
    _c, _d = _req("/api/apply", {"config": {"hack_me": 1}, "reload": False})
    ck("apply 非法键被拒（且没写文件）", _d.get("ok") is False, str(_d)[:80])
    _c, _d = _req("/api/apply", {"config": {"perception": {"max_chars": "abc"}}, "reload": False})
    ck("apply 类型错被拒", _d.get("ok") is False, str(_d)[:80])
finally:
    srv.shutdown()

print()
if FAIL:
    print("FAILED: %s" % ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
