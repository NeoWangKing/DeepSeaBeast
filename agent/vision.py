"""看图：把图片交给 DeepSeek 多模态模型（默认 deepseek-flash），返回中文描述。

不用 GLM —— 对话看图统一走 DeepSeek（GLM 只负责表情包打标）。
"""
import base64
import os

from . import llm as llm_mod

DEFAULT_VISION_MODEL = "deepseek-flash"


def vision_model(cfg: dict = None) -> str:
    c = ((cfg or {}).get("agent") or {}).get("llm") or {}
    return str(c.get("vision_model") or DEFAULT_VISION_MODEL)


def describe(paths, cfg: dict = None, question: str = "这张图里是什么？看到了什么就自然说说，一句话，别编。",
             max_tokens: int = 260) -> str:
    """paths: 本地图片路径列表。返回模型给的文字描述（失败返回空串）。"""
    parts = []
    for p in list(paths or [])[:2]:
        try:
            ext = os.path.splitext(p)[1].lstrip(".").lower() or "jpeg"
            if ext in ("gif", "webp", "bmp"):
                try:
                    import io
                    from PIL import Image as _PIL
                    im = _PIL.open(p)
                    try:
                        im.seek(0)
                    except Exception:
                        pass
                    buf = io.BytesIO()
                    im.convert("RGB").save(buf, format="JPEG", quality=85)
                    b64 = base64.b64encode(buf.getvalue()).decode()
                    ext = "jpeg"
                except Exception:
                    b64 = base64.b64encode(open(p, "rb").read()).decode()
            else:
                b64 = base64.b64encode(open(p, "rb").read()).decode()
            parts.append({"type": "image_url",
                          "image_url": {"url": "data:image/%s;base64,%s" % (ext, b64)}})
        except Exception:
            continue
    if not parts:
        return ""
    parts.append({"type": "text", "text": question})
    try:
        return llm_mod.chat_vision([{"role": "user", "content": parts}],
                                   model=vision_model(cfg), cfg=cfg, max_tokens=max_tokens)
    except Exception:
        return ""
