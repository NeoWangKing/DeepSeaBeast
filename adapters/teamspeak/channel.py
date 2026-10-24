"""TeamSpeak 通道：通过 TS6 的 **SSH Query** 收发频道文字消息。

为什么是 SSH Query（TS6 的变化）：
  - TS3 时代的经典 ServerQuery（TCP 10011）在 TS6 里没了
  - Web Query（HTTP 10080）**不支持 `servernotifyregister`**，收不到消息
  - SSH Query（10022）支持事件订阅，所以收发都走它

协议要点：
  连上后是一个交互式命令行，每条命令回 `error id=0 msg=ok`
  订阅频道文字      `servernotifyregister event=textchannel id=<cid>`
  收到              `notifytextmessage targetmode=2 msg=<escaped> invokerid=.. invokername=..`
  发送              `sendtextmessage targetmode=2 target=<cid> msg=<escaped>`

她在这里是**查询客户端**：出现在频道文字里，但不在成员列表、也不进语音。
"""

from __future__ import annotations

import asyncio
import logging
import re

from core.channel import ChatRef, InboundMessage, Message, OutboundMessage, SendResult, Sender

log = logging.getLogger("teamspeak")

_ESC = {"\\": "\\\\", " ": "\\s", "/": "\\/", "|": "\\p",
        "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_UNESC = {"s": " ", "/": "/", "p": "|", "n": "\n", "r": "\r",
          "t": "\t", "b": "\b", "f": "\f", "\\": "\\"}


def escape(text: str) -> str:
    return "".join(_ESC.get(ch, ch) for ch in str(text or ""))


def unescape(text: str) -> str:
    out: list[str] = []
    i = 0
    s = str(text or "")
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            out.append(_UNESC.get(s[i + 1], s[i + 1]))
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def parse_line(line: str) -> dict:
    """`a=1 b=2 c=x\\sy` → {a:'1', b:'2', c:'x y'}（值里的空格是 \\s，所以按空格切安全）"""
    d: dict[str, str] = {}
    for part in str(line or "").strip().split(" "):
        if "=" in part:
            k, v = part.split("=", 1)
            d[k] = unescape(v)
    return d


class TeamSpeakChannel:
    name = "teamspeak"
    abilities: set[str] = {"text"}       # 文字聊天：没有图片/表情/文件
    supports: set[str] = {"room"}

    def __init__(self, host: str = "127.0.0.1", port: int = 10022,
                 username: str = "serveradmin", password: str = "",
                 voice_port: int = 9987, channels: list[int] | None = None,
                 nickname: str = "大肥鱼", room_prefix: str = "ts",
                 follow_humans: bool = True, follow_interval: float = 15.0) -> None:
        self.host = str(host)
        self.port = int(port)
        self.username = str(username)
        self.password = str(password)
        self.voice_port = int(voice_port)
        self.channels = [int(c) for c in (channels or [])]
        self.nickname = str(nickname or "")
        self.room_prefix = str(room_prefix or "ts")
        self.self_account = self.nickname
        self._stop = asyncio.Event()
        self._proc = None
        self._lock = asyncio.Lock()
        self._lines: asyncio.Queue = asyncio.Queue()   # 读循环填，命令读
        self._self_id = ""
        self._names: dict[int, str] = {}
        self._nick_actual = self.nickname
        # TS 的频道文字只投递给"在该频道里"的客户端，所以她要跟着人走
        self.follow_humans = bool(follow_humans)
        self.follow_interval = max(5.0, float(follow_interval))
        self._cur_cid = 0
        self._last_clientlist = ""

    # ── 通道契约 ────────────────────────────────────────
    def describe(self, chat) -> str:
        nm = self._names.get(int(getattr(chat, "id", 0) or 0), "")
        return "TeamSpeak 频道「%s」的文字聊天" % nm if nm else "TeamSpeak 频道的文字聊天"

    def scope_info(self, chat) -> dict:
        cid = int(getattr(chat, "id", 0) or 0)
        return {"kind": "room", "id": cid, "key": "%s:%d" % (self.room_prefix, cid),
                "label": "TS 频道", "name": self._names.get(cid, "")}

    def identity(self, chat) -> dict:
        n = self._nick_actual or self.nickname
        return {"name": n, "aliases": [n] if n else [], "account": n}

    def profile(self) -> dict:
        return {"style": "这是 TeamSpeak 频道的文字聊天：纯文本，没有图片、表情包和 @。"
                         "说话的人都在语音频道里，消息会显示给该频道所有人。",
                "inline": {"faces": [], "mention": False, "markdown": False},
                "limits": {"max_chars": 1000}}

    async def start(self, on_message) -> None:
        self._stop.clear()
        backoff = 3.0
        while not self._stop.is_set():
            try:
                await self._run_once(on_message)
                backoff = 3.0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("teamspeak 连接断开：%r，%.0fs 后重连", e, backoff)
            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)

    async def stop(self) -> None:
        self._stop.set()
        p = self._proc
        if p is not None:
            try:
                p.close()
            except Exception:
                pass

    async def send(self, out: OutboundMessage) -> SendResult:
        text = (out.message.text or "").strip()
        if not text:
            return SendResult(ok=False, error="空消息不发", retryable=False)
        p = self._proc
        if p is None:
            return SendResult(ok=False, error="还没连上 TS 查询接口", retryable=True)
        cid = int(getattr(out.chat, "id", 0) or 0)
        # ⚠️ targetmode=2 时 TS **忽略 target**，消息发给"自己当前所在频道"。
        # 所以要先确保自己在目标频道里（踩过：消息发到了别的频道）
        if cid and cid != self._cur_cid:
            await self._move_to(proc, cid)
        line = "sendtextmessage targetmode=2 msg=%s" % escape(text.replace("\n", " "))
        async with self._lock:
            try:
                err = await self._cmd(p, line, timeout=5.0)
            except Exception as e:
                return SendResult(ok=False, error="写入失败：%r" % (e,), retryable=True)
            if not err:
                return SendResult(ok=False, error="TS 没回执（超时）", retryable=True)
        if not err.startswith("error id=0"):
            return SendResult(ok=False, error="TS 拒绝：%s" % err[:60], retryable=False)
        return SendResult(ok=True, confirmed=False, error="TS 文字没有回执，无法确认送达")

    # ── 内部 ────────────────────────────────────────────
    async def _run_once(self, on_message) -> None:
        import asyncssh

        async with asyncssh.connect(self.host, port=self.port, username=self.username,
                                    password=self.password, known_hosts=None) as conn:
            proc = await conn.create_process(encoding="utf-8")
            self._proc = proc
            reader = asyncio.create_task(self._read_loop(proc, on_message))
            try:
                # 先起读循环再握手：不然命令的回执没人接（踩过，握手全超时）
                await self._cmd(proc, "use port=%d" % self.voice_port)
                await self._learn_self_id(proc)
                await self._clean_stale(proc)
                await self._set_nickname(proc)
                await self._join_initial(proc)
                await self._subscribe(proc)
                if self.follow_humans:
                    asyncio.create_task(self._follow_loop(proc))
                log.info("teamspeak 已接入：频道 %s，昵称 %s",
                         self.channels or "全部", self._nick_actual)
                await reader
            finally:
                reader.cancel()
                self._proc = None
                try:
                    proc.close()
                except Exception:
                    pass

    async def _read_loop(self, proc, on_message) -> None:
        """唯一读流的地方：事件交出去，其它行进队列给命令读。"""
        buf = ""
        while not self._stop.is_set():
            chunk = await proc.stdout.read(8192)
            if not chunk:
                raise ConnectionError("TS 查询连接被关闭")
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                # TS 查询的行尾是 "\n\r"：按 \n 切开后下一行开头会残留 \r
                line = line.strip("\r")
                if not line:
                    continue
                if line.startswith("notify"):
                    if line.startswith("notifytextmessage"):
                        await self._on_text(line, on_message)
                    continue
                await self._lines.put(line)

    def _drain(self) -> None:
        while not self._lines.empty():
            try:
                self._lines.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _raw_cmd(self, proc, cmd: str, timeout: float = 4.0) -> str:
        """要读数据体的命令（channellist / whoami）：收集到 `error id=` 为止。"""
        self._drain()
        proc.stdin.write(cmd + "\n")
        await proc.stdin.drain()
        rows: list[str] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                line = await asyncio.wait_for(self._lines.get(), timeout=max(0.1, deadline - loop.time()))
            except asyncio.TimeoutError:
                break
            rows.append(line)
            if line.startswith("error id="):
                break
        return "\n".join(rows)

    async def _cmd(self, proc, cmd: str, timeout: float = 4.0) -> str:
        """发一条命令，只关心它自己的 error 回执。"""
        self._drain()
        proc.stdin.write(cmd + "\n")
        await proc.stdin.drain()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                line = await asyncio.wait_for(self._lines.get(), timeout=max(0.1, deadline - loop.time()))
            except asyncio.TimeoutError:
                break
            if line.startswith("error id="):
                return line
        return ""

    async def _learn_self_id(self, proc) -> None:
        me = await self._raw_cmd(proc, "whoami")
        m = re.search(r"client_id=(\d+)", me)
        if m:
            self._self_id = m.group(1)

    async def _clean_stale(self, proc) -> None:
        """踢掉自己上一次留下的僵尸查询会话。

        踩过：每次重启核心，旧的 SSH 会话在 TS 里还挂着（serveradmin2、大肥鱼（文字）…），
        越积越多，还会把昵称占掉导致 `nickname is already in use`。
        只清理**昵称以我们名字开头**的客户端，不碰别人的查询会话。
        """
        info = await self._raw_cmd(proc, "clientlist")
        if not info:
            return
        base = self.nickname or "大肥鱼"
        killed = 0
        # clientlist 的字段是 `clid=`（whoami 才是 client_id），别搞混
        for m in re.finditer(r"(?:clid|client_id)=(\d+)[^|]*?client_nickname=([^\s|]+)", info):
            cid, nick = m.group(1), unescape(m.group(2))
            if cid == self._self_id:
                continue
            if not nick.startswith(base):
                continue
            e = await self._cmd(proc, "clientkick clid=%s reasonid=5 reasonmsg=reconnect" % cid)
            if "id=0" in e:
                killed += 1
        if killed:
            log.info("清掉 %d 个自己遗留的查询会话", killed)
            await asyncio.sleep(0.4)

    async def _move_to(self, proc, cid: int) -> bool:
        me = await self._raw_cmd(proc, "whoami")
        m = re.search(r"client_id=(\d+)", me)
        if not m:
            return False
        e = await self._cmd(proc, "clientmove clid=%s cid=%d" % (m.group(1), cid))
        if "id=0" not in e:
            log.warning("切到频道 %d 失败：%s", cid, e[:50])
            return False
        self._cur_cid = cid
        await self._cmd(proc, "servernotifyregister event=textchannel id=%d" % cid)
        return True

    async def _set_nickname(self, proc) -> None:
        if not self.nickname:
            return
        err = await self._cmd(proc, "clientupdate client_nickname=%s" % escape(self.nickname))
        if "id=0" in err:
            self._nick_actual = self.nickname
            return
        # 昵称被占用（实测 error 513）：加后缀依次重试
        for alt in (self.nickname + "（文字）", self.nickname + "·bot"):
            err = await self._cmd(proc, "clientupdate client_nickname=%s" % escape(alt))
            if "id=0" in err:
                self._nick_actual = alt
                log.info("昵称 %s 被占用，改用 %s", self.nickname, alt)
                return
        log.info("设昵称没成功（%s），先用当前昵称", err[:40] or "超时")

    async def _join_initial(self, proc) -> None:
        """先进一个频道：优先配置的频道，其次"人最多的那个"。

        为什么必须进：TS 只把频道文字投递给**在该频道里的**客户端（实测：在外面订阅收不到）。
        """
        info = await self._raw_cmd(proc, "channellist")
        for m in re.finditer(r"cid=(\d+)[^|]*?channel_name=([^\s|]+)", info):
            self._names[int(m.group(1))] = unescape(m.group(2))
        cl = await self._raw_cmd(proc, "clientlist")
        self._last_clientlist = cl
        target = self.channels[0] if self.channels else self._busiest_human_channel()
        if not target:
            return
        me = await self._raw_cmd(proc, "whoami")
        m = re.search(r"client_id=(\d+)", me)
        my_clid = m.group(1) if m else "0"
        e = await self._cmd(proc, "clientmove clid=%s cid=%d" % (my_clid, target))
        if "id=0" in e:
            self._cur_cid = target
            log.info("进入频道 %d（%s）", target, self._names.get(target, "?"))

    def _humans_per_channel(self, clientlist: str) -> dict[int, int]:
        """每个频道里"真人客户端"的数量（client_type=0）。"""
        out: dict[int, int] = {}
        for m in re.finditer(r"cid=(\d+)[^|]*?client_type=(\d)", clientlist):
            cid, ctype = int(m.group(1)), int(m.group(2))
            if ctype == 0:
                out[cid] = out.get(cid, 0) + 1
        return out

    def _busiest_human_channel(self) -> int:
        humans = self._humans_per_channel(self._last_clientlist)
        if not humans:
            return 0
        return max(humans.items(), key=lambda kv: kv[1])[0]

    async def _follow_loop(self, proc) -> None:
        """定期看该待在哪个频道：有人说话就过去，避免来回跳。"""
        while not self._stop.is_set():
            await asyncio.sleep(self.follow_interval)
            try:
                info = await self._raw_cmd(proc, "clientlist")
                self._last_clientlist = info
                humans = self._humans_per_channel(info)
                if not humans:
                    continue
                here = humans.get(self._cur_cid, 0)
                best_cid, best_n = max(humans.items(), key=lambda kv: kv[1])
                if here > 0:
                    continue                       # 当前频道已经有人，别乱动
                me = await self._raw_cmd(proc, "whoami")
                m = re.search(r"client_id=(\d+)", me)
                if not m:
                    continue
                e = await self._cmd(proc, "clientmove clid=%s cid=%d" % (m.group(1), best_cid))
                if "id=0" in e:
                    self._cur_cid = best_cid
                    await self._cmd(proc, "servernotifyregister event=textchannel id=%d" % best_cid)
                    log.info("跟着人去频道 %d（%s，%d 人）",
                             best_cid, self._names.get(best_cid, "?"), best_n)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("跟随频道出错：%r", e)

    async def _subscribe(self, proc) -> None:
        info = await self._raw_cmd(proc, "channellist")
        for m in re.finditer(r"cid=(\d+)[^|]*?channel_name=([^\s|]+)", info):
            self._names[int(m.group(1))] = unescape(m.group(2))
        targets = self.channels or sorted(self._names)
        for cid in targets:
            e = await self._cmd(proc, "servernotifyregister event=textchannel id=%d" % cid)
            if "id=0" not in e:
                log.warning("订阅频道 %d 失败：%s", cid, e[:60])


    async def _on_text(self, line: str, on_message) -> None:
        d = parse_line(line)
        if str(d.get("targetmode") or "") != "2":
            return
        invoker = str(d.get("invokerid") or "")
        if invoker and self._self_id and invoker == self._self_id:
            return                                   # 自己发的会被回显，忽略
        text = str(d.get("msg") or "").strip()
        if not text:
            return
        name = str(d.get("invokername") or ("client%s" % invoker))
        try:
            cid = int(d.get("target") or 0)
        except Exception:
            cid = 0
        chat = ChatRef.room(cid, "%s:%d" % (self.room_prefix, cid))
        called = bool(self._nick_actual) and self._nick_actual in text
        msg = InboundMessage(chat=chat, sender=Sender(int(invoker or 0), name),
                             message=Message.from_text(text), message_id="",
                             addressed=called, addressed_by="name" if called else "")
        # ⚠️ 不能 await：回合要跑好几秒模型，读循环被卡住的话
        # 发送命令的 error 回执就没人读，会误判"发送超时"（踩过）
        asyncio.create_task(on_message(msg))
