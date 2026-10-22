#!/usr/bin/env python3
"""SSH 到服务器后，和**运行中的核心**聊天（走 unix socket，纯标准库）。

用法：
    python3 deploy/chat.py                  # 交互
    echo "在吗" | python3 deploy/chat.py    # 一次性
    python3 deploy/chat.py -s /tmp/deepsea.sock
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-s", "--socket", default="/tmp/deepsea.sock")
    a = ap.parse_args()

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(a.socket)
    except OSError as e:
        print("连不上 %s：%s" % (a.socket, e))
        print("（核心没开 [adapters.socket]，或路径不对）")
        return 2

    last = [time.time()]

    def reader() -> None:
        buf = b""
        while True:
            try:
                chunk = s.recv(65536)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    d = json.loads(line.decode("utf-8", "ignore"))
                except Exception:
                    continue
                print("鱼> " + str(d.get("text") or ""), flush=True)
                last[0] = time.time()
        done.set()

    threading.Thread(target=reader, daemon=True).start()

    start = time.time()
    interactive = sys.stdin.isatty()
    if interactive:
        print("（Ctrl-D 或 /quit 退出）")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line in ("/quit", "/exit"):
            break
        s.sendall((json.dumps({"text": line}, ensure_ascii=False) + "\n").encode())
        last[0] = time.time()
        if not interactive:
            time.sleep(0.1)
    if not interactive:
        # 等到"安静 4 秒"或者 60 秒上限，避免回复还在生成就退出
        deadline = time.time() + 60
        while time.time() < deadline:
            if time.time() - last[0] > 4 and last[0] > start:
                break
            time.sleep(0.2)
    s.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
