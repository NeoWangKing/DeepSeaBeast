"""CLI adapter：本地命令行直接和机器人聊天（开发用，也是"没有 QQ 也能跑"的证明）。"""

from .channel import CliChannel

__all__ = ["CliChannel"]
