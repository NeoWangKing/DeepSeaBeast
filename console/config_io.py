"""读写 `core__custom__.toml`：只动指定的键，保留注释，写完必须能重新解析。

设计约束（踩过的坑都写在这）：
  · 用户文件里有大量注释，**绝不能整体重写**——用行级替换
  · 只允许改 `*__custom__*` 文件（模板/仓库文件一律拒绝）
  · 写前备份、写后校验（解析 + 读回比对），任何一步失败就回滚
"""

from __future__ import annotations

import os
import re
import shutil
import time
import tomllib
from pathlib import Path

# 哪些段允许控制台改（核心层）
EDITABLE_SECTIONS = ("gate", "turn", "memory", "todos", "send", "pacing")

# 字段声明：类型 / 范围 / 文案（英文大写在左，中文在右，跟莱茵生命的字段双写一致）
FIELDS: dict[str, list[dict]] = {
    "gate": [
        {"key": "default_preset", "type": "enum", "options": ["strict", "balanced", "loose"],
         "label": "DEFAULT PRESET / 默认档位",
         "help": "strict 只理被叫到的；balanced 被叫必问、否则按打分；loose 每条都问模型"},
        {"key": "credit_max", "type": "float", "min": 0, "max": 20, "step": 0.5,
         "label": "CREDIT MAX / 发言额度上限",
         "help": "像「还能开口几次」，用完她会安静一阵"},
        {"key": "credit_refill_min", "type": "float", "min": 1, "max": 240, "step": 1,
         "label": "CREDIT REFILL / 额度回充分钟",
         "help": "每多少分钟回 1 点额度"},
        {"key": "score_threshold", "type": "float", "min": -5, "max": 20, "step": 0.5,
         "label": "SCORE THRESHOLD / 打分阈值",
         "help": "场面分超过它才值得问模型（带 ±1 抖动）"},
        {"key": "max_decide_per_hour", "type": "int", "min": 0, "max": 500,
         "label": "MAX DECIDE / 每小时问模型上限",
         "help": "成本硬上限，防止失控烧钱"},
        {"key": "dialogue_window_sec", "type": "float", "min": 0, "max": 600, "step": 5,
         "label": "DIALOGUE WINDOW / 连续对话窗口",
         "help": "这么久内同一个人继续说，就不重新打分"},
        {"key": "dialogue_max_turns", "type": "int", "min": 1, "max": 50,
         "label": "DIALOGUE TURNS / 对话最多来回",
         "help": "一轮对话最多几个来回"},
        {"key": "dialogue_refill_boost", "type": "float", "min": 1, "max": 10, "step": 0.5,
         "label": "DIALOGUE BOOST / 对话中回充加速",
         "help": "对话状态下额度回得更快"},
        {"key": "max_auto_per_hour", "type": "int", "min": 0, "max": 500,
         "label": "MAX REPLIES / 每小时最多回几条",
         "help": "每个会话每小时回复次数上限"},
        {"key": "typing_wait_sec", "type": "float", "min": 0, "max": 60, "step": 1,
         "label": "TYPING WAIT / 等她说完的等待",
         "help": "「话没说完」时每来一句就重新计时"},
        {"key": "typing_max_wait_sec", "type": "float", "min": 0, "max": 120, "step": 1,
         "label": "TYPING MAX / 一串话最多等多久",
         "help": "从第一句算起，超过就正常回"},
        {"key": "private_owner_only", "type": "bool",
         "label": "PRIVATE OWNER ONLY / 私聊只理主人",
         "help": "打开后陌生人的私聊不理"},
    ],
    "turn": [
        {"key": "max_rounds", "type": "int", "min": 1, "max": 32,
         "label": "MAX ROUNDS / 单回合最多几轮工具调用",
         "help": "防死循环"},
        {"key": "max_tokens", "type": "int", "min": 200, "max": 16000, "step": 100,
         "label": "MAX TOKENS / 单回合输出上限",
         "help": "只算输出 token"},
        {"key": "max_seconds", "type": "int", "min": 5, "max": 300,
         "label": "MAX SECONDS / 单回合墙钟上限",
         "help": "超时就停下"},
        {"key": "max_sends", "type": "int", "min": 1, "max": 20,
         "label": "MAX SENDS / 单回合最多发几条",
         "help": "防止一条消息刷屏"},
        {"key": "wait_recheck_sec", "type": "int", "min": 0, "max": 120,
         "label": "WAIT RECHECK / 迟迟重判间隔",
         "help": "她说「先等等」时，多久后再决定一次"},
    ],
    "memory": [
        {"key": "budget_tokens", "type": "int", "min": 1000, "max": 64000, "step": 500,
         "label": "BUDGET TOKENS / 上下文预算",
         "help": "超了先丢低优先级素材，规则与场景永不裁"},
    ],
    "todos": [
        {"key": "max_open", "type": "int", "min": 1, "max": 100,
         "label": "MAX OPEN / 每会话未完成上限",
         "help": "满了她会说忙不过来"},
        {"key": "stale_days", "type": "int", "min": 1, "max": 90,
         "label": "STALE DAYS / 旧条目保留天数"},
        {"key": "remind_per_day", "type": "int", "min": 0, "max": 50,
         "label": "REMIND PER DAY / 每天最多提醒几次"},
        {"key": "tick_sec", "type": "int", "min": 10, "max": 3600,
         "label": "TICK / 扫描间隔秒"},
    ],
    "send": [
        {"key": "max_retries", "type": "int", "min": 0, "max": 5,
         "label": "MAX RETRIES / 发送重试次数",
         "help": "只重试「明确失败且可重试」的"},
        {"key": "retry_backoff_sec", "type": "float", "min": 0.1, "max": 10, "step": 0.1,
         "label": "RETRY BACKOFF / 退避起始秒"},
    ],
    "pacing": [
        {"key": "enabled", "type": "bool", "label": "ENABLED / 拟人打字节奏"},
        {"key": "base_sec", "type": "float", "min": 0, "max": 10, "step": 0.1,
         "label": "BASE / 基础延迟秒"},
        {"key": "sec_per_char", "type": "float", "min": 0, "max": 1, "step": 0.005,
         "label": "PER CHAR / 每字延迟秒"},
        {"key": "max_sec", "type": "float", "min": 0, "max": 30, "step": 0.5,
         "label": "MAX / 单条最大延迟秒"},
        {"key": "jitter", "type": "float", "min": 0, "max": 1, "step": 0.05,
         "label": "JITTER / 随机抖动比例"},
    ],
}

SECTION_LABELS = {
    "gate": ("GATE / 门控", "决定「要不要问她」：档位、发言额度、场面打分"),
    "turn": ("TURN / 回合", "一轮对话的预算与上限"),
    "memory": ("MEMORY / 记忆", "上下文预算与素材裁剪"),
    "todos": ("TODOS / 待办", "她自己记的事与主动提醒"),
    "send": ("SEND / 发送", "发送失败的重试策略"),
    "pacing": ("PACING / 节奏", "拟人的打字延迟"),
}


class ConfigError(Exception):
    pass


def load_raw(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ConfigError("配置解析失败：%r" % (e,))


def _check_target(path: Path) -> None:
    if "__custom__" not in path.name:
        raise ConfigError("只允许修改 *__custom__* 文件（这是模板或仓库文件）：%s" % path)


def _section_span(lines: list[str], section: str) -> tuple[int, int] | None:
    """找出 `[section]` 的行范围（不含标题行），到下一个 `[` 顶层段为止。"""
    head = None
    for i, ln in enumerate(lines):
        if ln.strip() == "[%s]" % section:
            head = i
            break
    if head is None:
        return None
    end = len(lines)
    for j in range(head + 1, len(lines)):
        if lines[j].lstrip().startswith("[") and not lines[j].lstrip().startswith("[["):
            end = j
            break
    return head + 1, end


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return '"%s"' % value.replace('"', '\\"')
    if isinstance(value, float):
        return ("%g" % value)
    return str(value)


def apply_changes(path: str | Path, changes: dict[str, dict]) -> dict:
    """把 {section: {key: value}} 写进配置文件，返回 {section: {key: [旧, 新]}}。"""
    p = Path(path)
    _check_target(p)
    if not p.exists():
        raise ConfigError("配置文件不存在：%s" % p)

    before = load_raw(p)
    lines = p.read_text(encoding="utf-8").splitlines()
    applied: dict[str, dict] = {}

    for section, values in (changes or {}).items():
        if section not in EDITABLE_SECTIONS:
            raise ConfigError("这个段不允许在这里改：%s" % section)
        spec = {f["key"]: f for f in FIELDS.get(section, [])}
        span = _section_span(lines, section)
        if span is None:
            # 段不存在：整段追加到文件末尾
            lines.append("")
            lines.append("[%s]" % section)
            span = (len(lines), len(lines))
        start, end = span
        for key, raw in (values or {}).items():
            f = spec.get(key)
            if f is None:
                raise ConfigError("%s 段里没有这个键：%s" % (section, key))
            val = _coerce(f, raw)
            old = (before.get(section) or {}).get(key)
            applied.setdefault(section, {})[key] = [old, val]

            # 行级替换：保住行尾注释
            hit = False
            for i in range(start, end):
                m = re.match(r"^(\s*%s\s*=\s*)(.*)$" % re.escape(key), lines[i])
                if m:
                    # 保住行尾注释，并尽量对齐到原来那一列
                    rest = m.group(2)
                    new = m.group(1) + _fmt(val)
                    if "#" in rest:
                        col = lines[i].index("#")
                        pad = max(2, col - len(new))
                        new = new + " " * pad + lines[i][col:]
                    lines[i] = new
                    hit = True
                    break
            if not hit:
                lines.insert(end, "%s = %s" % (key, _fmt(val)))
                end += 1
                # 后面的段起点也要跟着挪
                for s2 in list(applied.keys()) + [section]:
                    pass

    text = "\n".join(lines) + "\n"
    # 写完必须能解析，且读回来跟预期一致
    try:
        after = tomllib.loads(text)
    except Exception as e:
        raise ConfigError("改完的配置解析不了，已放弃：%r" % (e,))
    for section, kv in applied.items():
        for key, (_old, new) in kv.items():
            got = (after.get(section) or {}).get(key)
            if got != new:
                raise ConfigError("校验失败：%s.%s 写进去是 %r，读回来是 %r" % (section, key, new, got))

    backup = p.with_name(p.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(p, backup)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        shutil.copystat(p, tmp)
    except OSError:
        pass
    os.replace(tmp, p)
    _prune_backups(p)
    return applied


def _coerce(f: dict, raw):
    t = f["type"]
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "on", "yes", "是")
    if t == "int":
        try:
            v = int(float(raw))
        except Exception:
            raise ConfigError("%s 需要整数，收到 %r" % (f["key"], raw))
    elif t == "float":
        try:
            v = float(raw)
        except Exception:
            raise ConfigError("%s 需要数字，收到 %r" % (f["key"], raw))
    elif t == "enum":
        v = str(raw).strip()
        if v not in f["options"]:
            raise ConfigError("%s 只能是 %s" % (f["key"], "/".join(f["options"])))
        return v
    else:
        return str(raw)
    if "min" in f and v < f["min"]:
        raise ConfigError("%s 不能小于 %s" % (f["key"], f["min"]))
    if "max" in f and v > f["max"]:
        raise ConfigError("%s 不能大于 %s" % (f["key"], f["max"]))
    return v


def _prune_backups(p: Path, keep: int = 10) -> None:
    baks = sorted(p.parent.glob(p.name + ".bak-*"))
    for old in baks[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass


def read_core(path: str | Path) -> dict:
    """给前端的核心配置视图（只含核心层，不碰通道/插件）。"""
    raw = load_raw(path)
    out = {}
    for section in EDITABLE_SECTIONS:
        cur = raw.get(section) or {}
        fields = []
        for f in FIELDS[section]:
            item = dict(f)
            item["value"] = cur.get(f["key"])
            fields.append(item)
        out[section] = {"label": SECTION_LABELS[section][0],
                        "desc": SECTION_LABELS[section][1],
                        "fields": fields}
    gate = raw.get("gate") or {}
    return {"sections": out,
            "chats": gate.get("chats") or {},
            "allowed_groups": gate.get("allowed_groups") or [],
            "meta": {"path": str(path),
                     "mtime": Path(path).stat().st_mtime if Path(path).exists() else 0,
                     "writable": Path(path).exists() and "__custom__" in Path(path).name}}
