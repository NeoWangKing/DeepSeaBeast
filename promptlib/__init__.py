"""提示词层：人格层 + 行为层 → 一份 system prompt。

用法（main.py）：
    text, meta = promptlib.build_system_prompt(
        plugin_dir=PLUGIN_DIR, cfg=self.cfg, chat_key=gid, private=private,
        caps={"vision": False, "search": False, "kb": True})

设计要点：
  · 人格层（人格卡 + 通用补丁）在前，行为层（协议/节奏/边界）在后 —— 稳定内容优先，
    利于 DeepSeek 前缀缓存；易变内容（资料库/表情清单/记忆）由调用方追加在末尾。
  · 每段都能单独覆盖或关掉，见 sections.py。
"""
from . import skills  # noqa: F401
from .persona import load_patch, load_persona, pick_file, resolve  # noqa: F401
from .sections import PARTICIPATION, SECTIONS, STICKER_LEVELS, build_behavior

__all__ = ["build_system_prompt", "build_behavior", "load_persona", "load_patch",
           "pick_file", "resolve", "SECTIONS", "PARTICIPATION", "STICKER_LEVELS",
           "skills"]


def build_system_prompt(*, plugin_dir: str, cfg: dict, chat_key: str = "",
                        private: bool = False, caps: dict = None,
                        bot_name: str = "") -> tuple:
    """返回 (system_prompt 文本, 元信息 dict)。"""
    pcfg = cfg.get("prompt") or {}
    if not pcfg:
        pcfg = {}
    persona, persona_src = load_persona(plugin_dir, cfg, chat_key, private)
    patch, patch_src = load_patch(plugin_dir, cfg, chat_key, private)
    ctx = {
        "plugin_dir": plugin_dir,
        "prompts_dir": plugin_dir,
        "cfg": cfg,
        "bot_name": bot_name or pcfg.get("bot_name") or "小鲸鱼",
        "participation": pcfg.get("participation") or "normal",
        "sticker_level": (cfg.get("stickers") or {}).get("encourage", 1),
        "persona": persona_src or "",
        "caps": caps or {},
    }
    behavior = build_behavior(ctx)
    blocks = [x for x in (persona, patch, behavior) if x]
    text = "\n\n".join(blocks)
    extra = pcfg.get("extra") or ""
    if str(extra).strip():
        text += "\n\n" + str(extra).strip()
    meta = {
        "persona": persona_src or "none",
        "patch": patch_src or "none",
        "sections": len(SECTIONS),
        "participation": ctx["participation"],
        "sticker_level": ctx["sticker_level"],
        "chars": len(text),
        "persona_chars": len(persona),
        "behavior_chars": len(behavior),
    }
    return text, meta
