# 常用命令（本地开发）
PY := .venv/bin/python

.PHONY: help chat bot selfcheck test clean

help:
	@echo "make chat       —— 命令行直接和机器人聊天（不用 QQ）"
	@echo "make bot        —— 以 OneBot 模式起核心（等 SnowLuma 连上来）"
	@echo "make selfcheck  —— 一体自测：核心 + 假 SnowLuma 往返"
	@echo "make test       —— 跑所有自测脚本"

chat:
	$(PY) -m core --channel cli

bot:
	$(PY) -m core --channel onebot

selfcheck:
	$(PY) tests/selfcheck_roundtrip.py

test: selfcheck
	@for f in tests/selfcheck_*.py; do echo "== $$f"; $(PY) "$$f" || exit 1; done

clean:
	find . -name __pycache__ -type d -prune -exec rm -r {} + 2>/dev/null || true
