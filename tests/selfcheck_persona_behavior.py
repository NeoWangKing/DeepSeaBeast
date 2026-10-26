#!/usr/bin/env python3
"""行为回归：她**还像不像这张卡**（会调真模型，几分钱）。

机制测试（其他 selfcheck）保证"逻辑对不对"，这个保证"人像不像"。
三条守门断言，都来自踩过的坑：

  1. 不自称别的名字   —— 换卡以后还自称"大肥鱼"就是没换过来
  2. 不复读上一句     —— 卡片例句被当成台词照抄过
  3. 遵守卡片的标点规矩 —— 卡片要求不用句号，输出里就不该有

用法：
  .venv/bin/python tests/selfcheck_persona_behavior.py            # 默认测调试群那张卡
  .venv/bin/python tests/selfcheck_persona_behavior.py --card Skadi
  .venv/bin/python tests/selfcheck_persona_behavior.py --probes 2 # 每个断言跑几次
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import find_config, load as load_config  # noqa: E402
from core.llm import LLMConfig, chat_tools  # noqa: E402

FAIL: list[str] = []


def ck(name: str, cond: bool, extra: str = "") -> None:
    print("  %-44s %s %s" % (name, "OK" if cond else "FAIL", extra[:70]))
    if not cond:
        FAIL.append(name)


def read_card(name: str) -> str:
    """按人格卡的规范读正文（export/system-prompt.txt 优先）。"""
    base = ROOT / "plugins/personas/cards" / name
    for rel in ("export/system-prompt.txt", "persona.md", "prompt.txt"):
        p = base / rel
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


TOOLS = [{"type": "function", "function": {
    "name": "send_message",
    "description": "在群里说一句话（一次一条，别说多条）",
    "parameters": {"type": "object", "properties": {
        "text": {"type": "string", "description": "要说的话"}}, "required": ["text"]}}}]


def ask(persona: str, scene: str, user: str, llm) -> str:
    """用真实管线的方式问一句：system=人格卡，user=场景+消息，让她调 send_message。"""
    messages = [{"role": "system", "content": persona},
                {"role": "user", "content": "%s\n\n%s" % (scene, user)}]
    resp = chat_tools(messages, TOOLS, llm, tool_choice="required", max_tokens=300)
    for call in resp.tool_calls or []:
        if call.name == "send_message":
            return str(call.arguments.get("text") or "")
    return resp.content or ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", default="groups/debug")
    ap.add_argument("--probes", type=int, default=1, help="每条断言跑几次")
    a = ap.parse_args()

    cfg_path = find_config(str(ROOT))
    cfg = load_config(cfg_path) if cfg_path else None
    llm = LLMConfig.from_dict(getattr(cfg, "llm", {}) or {})
    if not llm.resolve_key():
        print("跳过：没有模型 key（行为测试要真模型）")
        return 0

    persona = read_card(a.card)
    if not persona:
        print("读不到卡片：%s" % a.card)
        return 1
    print("卡片：%s（%d 字）｜模型：%s" % (a.card, len(persona), llm.model))
    scene = "当前场景：QQ群 大肥鱼🤖调试群。在这里你叫「大肥鱼」。正在说话的是 灵其啊（1）。"

    print()
    print("== 1. 不自称别的名字 ==")
    for i in range(a.probes):
        out = ask(persona, scene, "你是谁", llm)
        ck("回复里出现「斯卡蒂」", "斯卡蒂" in out, out)
        ck("没自称大肥鱼/小鲸鱼", not any(w in out for w in ("大肥鱼", "小鲸鱼")), out)
        ck("没自称 AI/助手", not any(w in out for w in ("AI", "助手", "模型", "DeepSeek")), out)

    print()
    print("== 2. 不复读上一句（例句不是台词）==")
    for i in range(a.probes):
        bad = "这个称呼，还是别在这里叫"
        scene2 = (scene + "\n场面：你刚刚才说过话。"
                  "\n你刚才说过：%s —— 别重复这些说法和反应" % bad)
        out = ask(persona, scene2, "蒂蒂", llm)
        ck("没原样复读那句话", bad not in out, out)

    print()
    print("== 3. 守卡片的标点规矩（不用句号）==")
    for i in range(a.probes):
        out = ask(persona, scene, "今天怎么没说话", llm)
        ck("输出里没有「。」", "。" not in out, out)

    print()
    if FAIL:
        print("FAILED: %s" % "、".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
