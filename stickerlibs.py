# -*- coding: utf-8 -*-
"""多平台表情库：QQ 用 data/stickers（老路径），微信用它自己那套 data/stickers_wx。

为什么「同一份代码两个实例」：stickers.py 里的 DATA/INDEX 是模块级全局，
把同一个文件用两个模块名各加载一次、再改各自实例的 DATA/INDEX，
就得到两个互不干扰的库，QQ 那边的老代码一行都不用动。

scope "" → 默认库（QQ，data/stickers），返回的就是插件里那个 stickers 模块
scope "wx" → 微信库（data/stickers_wx），并且把「推 QQ 表情面板」那几个函数
             换成安全空实现（微信没有这个面板，不能让它去连 SnowLuma）
"""
import importlib.util
import os
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
WX_DIR = "stickers_wx"          # 相对 data/
_LOCK = threading.Lock()
_CACHE = {}


def _load(name: str, sub_dir: str):
    path = os.path.join(HERE, "stickers.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if sub_dir:
        mod.DATA = os.path.join(mod.HERE, "data", sub_dir)
        mod.INDEX = os.path.join(mod.DATA, "index.json")
        # 微信侧：QQ 表情面板相关的动作一律空实现（不是禁用，是根本没这个面板）
        mod.add_face = lambda *a, **k: False
        mod.push_to_face = lambda *a, **k: 0
        mod.sync_account = lambda *a, **k: 0
        mod.fetch_faces = lambda *a, **k: []
        mod.reconcile_deletions = lambda *a, **k: {"removed": 0, "kept": 0}
    return mod


def for_scope(scope: str = ""):
    """取某个平台的表情库模块。scope: "" = 默认（QQ）；"wx" = 微信。"""
    try:
        from . import stickers as _default          # 包模式（插件正常加载方式）
    except Exception:
        import stickers as _default                 # 脚本/单文件模式
    key = str(scope or "")
    if not key:
        return _default
    with _LOCK:
        mod = _CACHE.get(key)
        if mod is None:
            mod = _load("stickers_scope_" + key.replace("-", "_"),
                        WX_DIR if key == "wx" else ("stickers_" + key))
            _CACHE[key] = mod
        return mod


def wx_dir() -> str:
    """微信库目录（给体检/定时用）。"""
    return os.path.join(HERE, "data", WX_DIR)
