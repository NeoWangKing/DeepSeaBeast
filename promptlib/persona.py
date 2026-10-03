"""人格层：读“你是谁”（人格卡 + 通用人格补丁）。

人格卡放 prompts/personas/<文件名>.md（推荐），也兼容旧位置：
  · cfg.prompt.persona_by_group = {"<群号>|private": "文件名.md"}   ← 新写法
  · cfg.prompt_by_group        = {"<群号>|private": "system_prompt_friend.txt"}  ← 旧写法，继续可用
  · cfg.system_prompt_file     = 默认人格卡（默认 system_prompt.txt）

找不到文件就返回空串（由上层决定要不要兜底），不抛异常。
"""
import os

DIRS = ("prompts/personas", "prompts", "")


def resolve(plugin_dir: str, name: str) -> str:
    """按 名字 / 名字.md / 名字.txt 在几个目录里找；绝对路径直接认。"""
    if not name:
        return ""
    name = str(name).strip()
    if not name:
        return ""
    if os.path.isabs(name) and os.path.isfile(name):
        return name
    for d in DIRS:
        for cand in (name, name + ".md", name + ".txt"):
            p = os.path.join(plugin_dir, d, cand) if d else os.path.join(plugin_dir, cand)
            if os.path.isfile(p):
                return p
    return ""


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def pick_file(plugin_dir: str, cfg: dict, chat_key: str = "", private: bool = False) -> tuple:
    """返回 (路径, 来源说明)。"""
    key = str(chat_key or "")
    keys = []
    if private:
        if key and key not in ("private", "default"):
            keys.append(key)          # 优先按人（p:<uid>）
        keys.append("private")        # 再退回"所有私聊共用"
    else:
        keys.append(key)
    keys.append("default")
    pcfg = cfg.get("prompt") or {}

    for _src, _map in (("persona_by_group", pcfg.get("persona_by_group") or {}),
                       ("prompt_by_group", cfg.get("prompt_by_group") or {})):
        if not isinstance(_map, dict):
            continue
        for k in keys:
            if k and _map.get(k):
                p = resolve(plugin_dir, _map.get(k))
                if p:
                    return p, _src

    p = resolve(plugin_dir, cfg.get("system_prompt_file") or "system_prompt.txt")
    return (p, "default") if p else ("", "missing")


def load_persona(plugin_dir: str, cfg: dict, chat_key: str = "", private: bool = False) -> tuple:
    """返回 (人格卡正文, 来源说明)。"""
    path, src = pick_file(plugin_dir, cfg, chat_key, private)
    text = _read(path) if path else ""
    if text:
        return text, "%s:%s" % (src, os.path.basename(path))
    return "", src


def load_patch(plugin_dir: str, cfg: dict, chat_key: str = "", private: bool = False) -> tuple:
    """通用人格补丁（AI 味黑名单 / 回复示例 / 群文化自适应），对所有群生效。

    cfg.prompt.persona_patch_by_group = {"<群号>|private": "另一个补丁文件"} 可覆盖；
    值写成 "" / false 表示这个群不加补丁。
    """
    pcfg = cfg.get("prompt") or {}
    key = "private" if private else str(chat_key or "")
    m = pcfg.get("persona_patch_by_group") or {}
    name = m.get(key) if isinstance(m, dict) and key in m else None
    if name is None:
        name = pcfg.get("persona_patch", "personas/_common_patch.txt")
    if name in (None, "", False):
        return "", "off"
    path = resolve(plugin_dir, str(name))
    if not path:
        return "", "missing"
    text = _read(path)
    return (text, os.path.basename(path)) if text else ("", "empty")
