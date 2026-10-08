# -*- coding: utf-8 -*-
"""把一个仓库（GitHub / 本地目录）收进她的资料库，供某个群/某个人格专用。

用法：
    python3 tools/kb_import_repo.py https://github.com/user/repo [域名]     # 克隆并导入
    python3 tools/kb_import_repo.py /path/to/repo myproj                  # 本地目录导入
    python3 tools/kb_import_repo.py --list                                # 看有哪些域
    python3 tools/kb_import_repo.py --rm myproj                           # 删掉某个域

说明：
- 域名默认取仓库名；落到 data/kb/<域名>/，并写 _scope.json（记录来源/commit/时间）
- 只收文本类文件（文档 + 常见代码/配置后缀），跳过 node_modules/.git/dist 等
- 导入完自动重建该域索引；默认域不受影响（默认域会跳过带 _scope.json 的子目录）
- 之后在 config.json 里指定谁用这个域：
    "kb": {"scope_by_group": {"<群号>": "myproj"}, "scope_by_persona": {"project_assistant.txt": "myproj"}}
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import kb as KB  # noqa: E402

MAX_FILE = 400 * 1024        # 单文件上限 400KB
MAX_FILES = 3000             # 单仓库最多收这么多文件


def _scopes() -> list:
    out = []
    try:
        for d in sorted(os.listdir(KB.DATA)):
            p = os.path.join(KB.DATA, d)
            if os.path.isdir(p) and KB.is_scope_dir(p):
                try:
                    m = json.load(open(os.path.join(p, "_scope.json"), encoding="utf-8"))
                except Exception:
                    m = {}
                out.append((d, m))
    except Exception:
        pass
    return out


def _iter_files(root: str):
    exts = KB.DOC_EXTS + KB.CODE_EXTS
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in KB.SKIP_DIRS and not d.startswith(".")]
        for f in files:
            p = os.path.join(cur, f)
            ext = os.path.splitext(f)[1].lower()
            if ext not in exts and f.lower() not in ("dockerfile", "makefile", "readme", "license"):
                continue
            try:
                if os.path.getsize(p) > MAX_FILE:
                    continue
            except Exception:
                continue
            yield p


def _texty(path: str) -> bool:
    """粗略判断是不是文本（避免把二进制拷进去）。"""
    try:
        with open(path, "rb") as f:
            chunk = f.read(4096)
    except Exception:
        return False
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
        return True
    except Exception:
        try:
            chunk.decode("gb18030")
            return True
        except Exception:
            return False


def import_repo(repo: str, scope: str = "", verbose: bool = True) -> dict:
    scope = str(scope or "").strip() or os.path.basename(str(repo).rstrip("/")).replace(".git", "")
    if not scope or scope.startswith("/"):
        scope = "repo%d" % int(time.time())
    tmp = os.path.join("/tmp", "dsb-kb-" + scope)
    src_dir, commit = str(repo), ""
    is_url = str(repo).startswith(("http://", "https://", "git@"))
    try:
        if is_url:
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
            subprocess.run(["git", "clone", "--depth", "1", str(repo), tmp],
                           check=True, capture_output=True, timeout=600)
            src_dir = tmp
        commit = (subprocess.run(["git", "-C", src_dir, "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True).stdout or "").strip()
    except Exception as e:
        if verbose:
            print("!! 取仓库失败：%r" % (e,))
        return {"ok": False, "error": str(e)[:120]}
    dst = os.path.join(KB.DATA, scope)
    os.makedirs(dst, exist_ok=True)
    n_files = n_skip = 0
    for p in _iter_files(src_dir):
        if n_files >= MAX_FILES:
            break
        rel = os.path.relpath(p, src_dir)
        if rel.startswith(".."):
            continue
        if not _texty(p):
            n_skip += 1
            continue
        dp = os.path.join(dst, rel)
        try:
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            shutil.copyfile(p, dp)
            n_files += 1
        except Exception:
            n_skip += 1
    meta = {"name": scope, "repo": str(repo), "commit": commit, "imported_at": int(time.time()),
            "files": n_files, "skipped": n_skip}
    json.dump(meta, open(os.path.join(dst, "_scope.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    res = KB.build(scope, verbose=False)
    if verbose:
        print("导入完成：域=%s 文件=%d（跳过 %d）commit=%s → 索引 %s"
              % (scope, n_files, n_skip, commit or "-", res))
    if is_url and os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    return {"ok": True, "scope": scope, "files": n_files, "index": res}


def main() -> int:
    ap = argparse.ArgumentParser(description="把仓库收进资料库（按域隔离）")
    ap.add_argument("repo", nargs="?", default="")
    ap.add_argument("scope", nargs="?", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--rm", default="")
    a = ap.parse_args()
    if a.list:
        rows = _scopes()
        if not rows:
            print("还没有任何域（默认域是 data/kb 下的散文件）")
        for name, m in rows:
            print("  %-20s 文件 %-5s commit %-10s %s" % (name, m.get("files"),
                                                        m.get("commit") or "-",
                                                        str(m.get("repo"))[:60]))
        return 0
    if a.rm:
        p = KB.scope_dir(a.rm)
        if os.path.isdir(p):
            shutil.rmtree(p)
            print("已删除域：", a.rm)
        else:
            print("没有这个域：", a.rm)
        return 0
    if not a.repo:
        ap.print_help()
        return 1
    r = import_repo(a.repo, a.scope)
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
