#!/usr/bin/env python3
"""分层守卫：核心不认识任何具体聊天软件。

这是"核心像人、通道只是工具"这条原则的**自动化检查**：
  1. core/ 的提示词与逻辑里不能出现平台名词（QQ/群号/telnet/onebot/snowluma）
  2. core/ 不能 import adapters/ 或 plugins/
  3. 场景描述必须由 adapter 提供（describe），核心只拼装
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-50s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        FAIL.append(name)


# 平台名词：出现在**字符串字面量**里才算违规（注释里解释分层是允许的）
PLATFORM_WORDS = ("qq", "群号", "telnet", "onebot", "snowluma", "微信", "discord")
# core/ 里唯一允许提到通道名的位置：main.py 的 `from adapters import ...` 那一行（已在依赖检查里排除）
# 允许出现的文件：channel.py / channel_manager.py 的分层说明在注释里，
# 但它们的字符串也要检查；config.py 的 [adapters.*] 是通用键名，不含平台词。


def string_literals(path: Path) -> list[str]:
    """只取"真正会被用到的"字符串（跳过 docstring —— 那里解释分层是允许的）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            out.append(node.value)
    return out


def main() -> int:
    print("== core/ 的字符串里没有平台名词 ==")
    bad: list[str] = []
    for p in sorted((ROOT / "core").rglob("*.py")):
        for lit in string_literals(p):
            low = lit.lower()
            for w in PLATFORM_WORDS:
                if w in low:
                    bad.append("%s: %r" % (p.relative_to(ROOT), lit[:60]))
                    break
    ck("core 提示词/逻辑不含平台名词", not bad, "；".join(bad[:3]))

    print()
    print("== core/ 不依赖 adapters / plugins ==")
    deps: list[str] = []
    for p in sorted((ROOT / "core").rglob("*.py")):
        if p.name == "main.py":
            continue          # main.py 是"装配层"：唯一允许 import adapter 的地方
        src = p.read_text(encoding="utf-8")
        for m in re.finditer(r"^\s*(?:from|import)\s+(adapters|plugins)\b", src, re.M):
            deps.append("%s: %s" % (p.relative_to(ROOT), m.group(0).strip()))
    ck("除 main.py 外 core 没有反向依赖", not deps, "；".join(deps[:3]))

    print()
    print("== 行内占位符只有一个来源 ==")
    import re as _re
    defs = []
    for p in sorted((ROOT / "core").rglob("*.py")):
        if "FACE_TOKEN_TEMPLATE" in p.read_text(encoding="utf-8"):
            defs.append(p.name)
    ck("核心只在一处定义表情占位符", defs == ["capability.py"], str(defs))
    hard = []
    for p in sorted((ROOT / "adapters").rglob("*.py")):
        src = p.read_text(encoding="utf-8")
        if "[表情:" in src and "face_token" not in src and "FACE_TOKEN_RE" not in src:
            hard.append(p.name)
    ck("adapter 复用核心的占位符，不自己拼", not hard, str(hard))

    print()
    print("== 场景由 adapter 提供 ==")
    from core.channel import ChatRef, InboundMessage, Message, Sender
    from core.channel_manager import ChannelManager
    from core.prompt import build_scene

    class Ch:
        name = "fake"
        supports = {"local", "group"}
        abilities = {"text"}
        self_account = "42"

        def describe(self, chat):
            return "某个奇怪的地方 %s" % chat.id

        async def start(self, on):
            pass

        async def stop(self):
            pass

        async def send(self, out):
            pass

    mgr = ChannelManager()
    mgr.add(Ch())
    where = mgr.describe(ChatRef.group(7))
    ck("manager 转发 describe", "某个奇怪的地方 7" in where, where)
    ck("manager 暴露 self_account", mgr.self_account == "42", mgr.self_account)

    msg = InboundMessage(chat=ChatRef.group(7), sender=Sender(9, "某人"),
                         message=Message.from_text("在吗"), message_id="1", at_me=True)
    scene = build_scene(msg, where=where, self_account=mgr.self_account)
    ck("场景里用的是 adapter 给的位置", "某个奇怪的地方 7" in scene, scene)
    ck("没有硬编码平台名", "QQ" not in scene)

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
