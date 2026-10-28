"""大肥鱼控制台：核心层页面（纯标准库，不开新端口，只绑回环）。

为什么要独立进程而不是塞进核心里：
  · 核心在跑对话，控制台不该跟它抢同一个进程/日志/重启节奏
  · 控制台只读配置与数据文件、只写 `*__custom__*`，不 import 核心业务逻辑
  · 因此它天然符合分层：核心不认识控制台，控制台也不驱动核心

用法：
  python -m console.server                      # 127.0.0.1:8765
  python -m console.server --port 8765 --config /opt/deepsea/core__custom__.toml
      （可选 HTTP Basic 认证：--user admin --password 留空则从环境变量读）
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from console import config_io  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
VERSION = "0.1.0"


def _find_config(start: Path) -> Path:
    for name in ("core__custom__.toml", "core.toml"):
        p = start / name
        if p.exists():
            return p
    return start / "core__custom__.toml"


class State:
    config_path: Path
    data_dir: Path
    user: str = ""
    password: str = ""

    @classmethod
    def setup(cls, config_path: str, user: str, password: str) -> None:
        cls.config_path = Path(config_path).resolve()
        cls.data_dir = cls.config_path.parent / "data"
        cls.user = user
        cls.password = password


def _service_status() -> dict:
    """核心服务的运行状态（读 systemd；读不到就标未知）。"""
    out = {"active": "unknown", "memory_mb": 0, "since": "", "pid": 0}
    try:
        r = subprocess.run(["systemctl", "show", "-p", "ActiveState,MainPID,MemoryCurrent",
                            "deepsea-beast"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            k, _, v = line.partition("=")
            if k == "ActiveState":
                out["active"] = v.strip()
            elif k == "MainPID":
                out["pid"] = int(v or 0)
            elif k == "MemoryCurrent" and v.strip().isdigit():
                out["memory_mb"] = round(int(v) / 1048576, 1)
    except Exception:
        pass
    return out


def _counts() -> dict:
    """各类数据的条数（只数文件，不解析业务）。"""
    d = State.data_dir
    out = {"recent": 0, "stickers": 0, "pending_stickers": 0, "memory_entries": 0, "todos": 0}
    try:
        out["recent"] = len(list((d / "recent").glob("*.json")))
    except Exception:
        pass
    try:
        idx = json.loads((d / "plugins/stickers/index.json").read_text(encoding="utf-8"))
        items = idx.get("items") or []
        out["stickers"] = len(items)
        out["pending_stickers"] = sum(1 for x in items if x.get("status") == "pending")
    except Exception:
        pass
    try:
        total = 0
        for f in (d / "plugins/memory/spaces").glob("*.json"):
            total += len(json.loads(f.read_text(encoding="utf-8")).get("entries") or [])
        out["memory_entries"] = total
    except Exception:
        pass
    try:
        for f in (d / "todos").glob("*.json"):
            items = json.loads(f.read_text(encoding="utf-8")).get("items") or []
            out["todos"] += sum(1 for x in items if x.get("status") == "open")
    except Exception:
        pass
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "DeepSeaConsole/" + VERSION

    # ── 认证 ──
    def _authed(self) -> bool:
        if not State.user:
            return True                     # 没配认证 = 只绑回环时的默认
        head = self.headers.get("Authorization") or ""
        if not head.startswith("Basic "):
            return False
        try:
            dec = base64.b64decode(head[6:]).decode("utf-8")
        except Exception:
            return False
        return dec == "%s:%s" % (State.user, State.password)

    def _deny(self) -> None:
        body = b"auth required"
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="deepsea"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── 输出 ──
    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, rel: str, ctype: str) -> None:
        p = STATIC / rel
        if not p.is_file():
            self._json({"ok": False, "error": "not found"}, 404)
            return
        body = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── 路由 ──
    def do_GET(self) -> None:  # noqa: N802
        if not self._authed():
            self._deny()
            return
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if path in ("/", "/index.html"):
            self._file("index.html", "text/html; charset=utf-8")
            return
        if path == "/app.css":
            self._file("rhine.css", "text/css; charset=utf-8")
            return
        if path == "/app.js":
            self._file("app.js", "application/javascript; charset=utf-8")
            return
        if path == "/api/status":
            self._json({"ok": True, "service": _service_status(), "counts": _counts(),
                        "version": VERSION, "time": time.time()})
            return
        if path == "/api/core":
            try:
                self._json({"ok": True, **config_io.read_core(State.config_path)})
            except config_io.ConfigError as e:
                self._json({"ok": False, "error": str(e)}, 400)
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        if not self._authed():
            self._deny()
            return
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/core":
            self._json({"ok": False, "error": "not found"}, 404)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            self._json({"ok": False, "error": "请求体不是合法 JSON：%r" % (e,)}, 400)
            return
        try:
            applied = config_io.apply_changes(State.config_path, payload.get("changes") or {})
        except config_io.ConfigError as e:
            self._json({"ok": False, "error": str(e)}, 400)
            return
        except Exception as e:
            self._json({"ok": False, "error": "写入失败：%r" % (e,)}, 500)
            return
        self._json({"ok": True, "applied": applied,
                    "note": "已写入配置文件；核心需要重启才会生效"})

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[console] %s %s\n" % (self.address_string(), fmt % args))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="默认只绑回环（配合 nginx 反代）")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--config", default="", help="默认自动找 core__custom__.toml")
    ap.add_argument("--user", default=os.environ.get("DSB_CONSOLE_USER", ""))
    ap.add_argument("--password", default=os.environ.get("DSB_CONSOLE_PASSWORD", ""))
    a = ap.parse_args()

    cfg = Path(a.config) if a.config else _find_config(Path.cwd())
    State.setup(str(cfg), a.user, a.password)
    if a.host not in ("127.0.0.1", "localhost", "::1") and not a.user:
        print("拒绝启动：绑定非回环地址时必须设置 --user/--password", file=sys.stderr)
        return 2

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print("大肥鱼控制台 http://%s:%d  （配置：%s）" % (a.host, a.port, State.config_path))
    if a.user:
        print("已启用 HTTP Basic 认证（用户 %s）" % a.user)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
