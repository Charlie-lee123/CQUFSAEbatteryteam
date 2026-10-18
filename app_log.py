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
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG = logging.getLogger("serialassistant")
_LOG.setLevel(logging.DEBUG)          # 级别闸门在 handler 上，logger 全放行
_LOG.propagate = False                # 别冒泡到 root，避免重复输出

_FILE: RotatingFileHandler | None = None
_PATH: Path | None = None


# 用户数据（日志 + 接收历史）放在**用户目录**，不跟安装目录走。
# ⛔ 为什么不能放程序旁边：升级时安装器可能先 DelTree 旧目录（换目录安装），
#    卸载器也会删 {app}\logs —— 用户的接收历史会跟着没了（2026-10-18 用户点名：
#    "每次更新的时候用户数据要保留，包括历史读取数据"）。
APP_DIR_NAME = "CQUFSAE-SerialAssistant"
# 旧版本放在程序目录里的文件：首次运行时搬到新位置（不覆盖已有数据）
_LEGACY_FILES = ("serialassistant.log", "serialassistant.log.1",
                 "serialassistant.log.2", "serialassistant.log.3",
                 "接收历史.log")


def _data_root() -> Path:
    """用户数据根目录：Windows 用 %LOCALAPPDATA%，其它系统 ~/.serialassistant。"""
    local = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local:
        return Path(local) / APP_DIR_NAME
    return Path.home() / ".serialassistant"


def _program_dir() -> Path:
    base = (Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False) else Path(__file__).resolve().parent)
    return base


def _migrate_legacy(target: Path) -> None:
    """把旧版放在程序目录里的日志/接收历史搬过来（只搬一次，绝不覆盖新数据）。"""
    import shutil
    legacy = _program_dir() / "logs"
    try:
        if not legacy.is_dir() or legacy.resolve() == target.resolve():
            return
    except OSError:
        return
    for name in _LEGACY_FILES:
        source, dest = legacy / name, target / name
        if not source.exists() or dest.exists():
            continue
        try:
            shutil.copy2(source, dest)
        except OSError:
            pass                      # 搬不动就留着旧文件，不影响使用


def _pick_dir() -> Path | None:
    """选日志目录：**用户目录优先**（升级/换目录安装都不会丢），失败再退回。"""
    candidates = [_data_root() / "logs", Path.home() / ".serialassistant" / "logs"]
    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue                  # 只读盘符等：换下一个候选
        _migrate_legacy(directory)
        return directory
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
