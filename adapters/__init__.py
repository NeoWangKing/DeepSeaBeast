"""通道装配层：**唯一知道"有哪些 adapter"的地方**。

核心只 import 这里的 `build_channels(cfg, owner_ids)`，它不知道 cli/onebot/tcp
这些名字——加一个新通道只改这个文件。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("adapters")


def spawn(coro) -> asyncio.Task:
    """把一次消息处理丢到后台跑：这样她说话时用户还能再发（打断）。"""
    task = asyncio.create_task(coro)
    task.add_done_callback(lambda t: t.exception() and
                           log.error("消息处理异常：%r", t.exception()))
    return task

__all__ = ["build_channels", "AVAILABLE"]


def AVAILABLE() -> list[str]:
    return ["cli", "onebot", "tcp", "socket"]


def build_channels(cfg: Any, owner_ids: list[int], only: str = "") -> list:
    """按配置装通道。配置形如 [adapters.<名字>] enabled = true。

    only：只启用指定的那一个（本地开发常用 `--channel cli`）。
    """
    ad = dict(getattr(cfg, "adapters", {}) or {})
    if only:
        ad = {only: {"enabled": True}}
    out: list = []

    if (ad.get("cli") or {}).get("enabled"):
        from .cli import CliChannel
        cli_ch = CliChannel(user_name="本地用户",
                            owner_id=(owner_ids[0] if owner_ids else 0))
        cli_ch.self_name = str((ad.get("cli") or {}).get("self_name", ""))
        out.append(cli_ch)

    br = ad.get("breamer") or {}
    if br.get("enabled"):
        from .breamer import BreamerChannel
        token = str(br.get("token") or "")
        tf = str(br.get("token_file") or "")
        if not token and tf:
            try:
                token = open(tf, encoding="utf-8").read().strip()
            except Exception:
                token = ""
        out.append(BreamerChannel(host=str(br.get("host", "127.0.0.1")),
                                  port=int(br.get("port", 6969)),
                                  token=token,
                                  room_key=str(br.get("room_key", "breamer")),
                                  room_name=str(br.get("room_name", "聊天室")),
                                  self_name=str(br.get("self_name", ""))))

    ts = ad.get("teamspeak") or {}
    if ts.get("enabled"):
        from .teamspeak import TeamSpeakChannel
        pw = str(ts.get("password") or "")
        pf = str(ts.get("password_file") or "")
        if not pw and pf:
            try:
                pw = open(pf, encoding="utf-8").read().strip()
            except Exception:
                pw = ""
        out.append(TeamSpeakChannel(host=str(ts.get("host", "127.0.0.1")),
                                    port=int(ts.get("port", 10022)),
                                    username=str(ts.get("username", "serveradmin")),
                                    password=pw,
                                    voice_port=int(ts.get("voice_port", 9987)),
                                    channels=[int(x) for x in (ts.get("channels") or [])],
                                    nickname=str(ts.get("nickname", "大肥鱼")),
                                    room_prefix=str(ts.get("room_prefix", "ts"))))

    ob = ad.get("onebot") or {}
    if ob.get("enabled"):
        from .onebot import OneBotChannel
        ids = list(ob.get("allow_self_ids") or [])
        out.append(OneBotChannel(host=str(ob.get("host", "127.0.0.1")),
                                 port=int(ob.get("port", 6199)),
                                 allow_self_ids=ids, owner_ids=owner_ids,
                                 bot_id=int(ob.get("bot_id", 0) or (ids[0] if ids else 0)),
                                 verify_send=bool(ob.get("verify_send", True)),
                                 verify_timeout_ms=int(ob.get("verify_timeout_ms", 3000))))

    tc = ad.get("tcp") or {}
    if tc.get("enabled"):
        from .tcp import TcpChannel
        tcp_ch = TcpChannel(host=str(tc.get("host", "127.0.0.1")),
                            port=int(tc.get("port", 4000)),
                            token=str(tc.get("token", "")))
        tcp_ch.self_name = str(tc.get("self_name", ""))
        out.append(tcp_ch)

    sk = ad.get("socket") or {}
    if sk.get("enabled"):
        from .socket import SocketChannel
        sock_ch = SocketChannel(path=str(sk.get("path", "/tmp/deepsea.sock")))
        sock_ch.self_name = str(sk.get("self_name", ""))
        out.append(sock_ch)

    if not out:
        raise SystemExit("没有启用任何通道，检查 [adapters.*] 配置（可选：%s）"
                         % "、".join(AVAILABLE()))
    return out
