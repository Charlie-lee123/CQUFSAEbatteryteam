"""分级日志模块。

- **分级输出**：DEBUG（每帧收发细节，默认不落盘）/ INFO（关键操作：开关串口、
  发送、设置变更、主题切换）/ WARNING / ERROR（打开失败、剪贴板异常等）。
  文件 handler 默认收 INFO 及以上 —— 每帧一条 DEBUG 在数据流场景下会刷爆文件；
  排查问题时把 ``_FILE.setLevel(logging.DEBUG)`` 打开即可看到完整收发流。
- **写入文件**：``logs/serialassistant.log``，滚动 1MB × 3 份，绝不可能把磁盘
  写满。目录优先放在程序旁边（开发时是项目目录、打包后是 exe 所在目录），
  只读（如 Program Files）就退回用户主目录。
- **谁在用**：main.py 启动时 ``init()`` 一次；主窗口在各关键点直接调
  ``debug/info/warning/error``。日志只是旁路 —— 写不进去也不能影响功能，
  所以所有文件操作失败都静默降级。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG = logging.getLogger("serialassistant")
_LOG.setLevel(logging.DEBUG)          # 级别闸门在 handler 上，logger 全放行
_LOG.propagate = False                # 别冒泡到 root，避免重复输出

_FILE: RotatingFileHandler | None = None
_PATH: Path | None = None


def _pick_dir() -> Path | None:
    """选日志目录：程序旁边优先（跟着软件走，好找），失败退回用户主目录。"""
    if getattr(sys, "frozen", False):
        candidates = [Path(sys.executable).resolve().parent]
    else:
        candidates = [Path(__file__).resolve().parent]
    candidates.append(Path.home() / ".serialassistant")
    for base in candidates:
        try:
            directory = base / "logs"
            directory.mkdir(parents=True, exist_ok=True)
            return directory
        except OSError:
            continue                  # 只读盘符等：换下一个候选
    return None


def init() -> str | None:
    """建立日志文件（幂等，多次调用无害）。返回日志文件路径，失败返回 None。"""
    global _FILE, _PATH
    if _FILE is not None:
        return str(_PATH)
    directory = _pick_dir()
    if directory is None:
        return None
    try:
        _PATH = directory / "serialassistant.log"
        _FILE = RotatingFileHandler(_PATH, maxBytes=1_000_000, backupCount=3,
                                    encoding="utf-8")
        _FILE.setLevel(logging.INFO)
        _FILE.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        _LOG.addHandler(_FILE)
        return str(_PATH)
    except OSError:
        _FILE = None
        _PATH = None
        return None


def path() -> str | None:
    return str(_PATH) if _PATH else None


def debug(message: str) -> None:
    _LOG.debug(message)


def info(message: str) -> None:
    _LOG.info(message)


def warning(message: str) -> None:
    _LOG.warning(message)


def error(message: str) -> None:
    _LOG.error(message)
