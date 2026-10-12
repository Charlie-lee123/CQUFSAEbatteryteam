# -*- coding: utf-8 -*-
"""历史接收数据：自动保存最近 5000 行接收内容（含时间戳行）。

- **存哪**：与日志文件同一目录（``app_log`` 的 logs 目录，跟着软件走）。
- **存什么**：接收区实际显示的行——已含时间戳行、段重排后的数据行；
  HEX 视图存的就是 HEX 文本。逐帧喂 ``feed()``，尾部换行由这里统一收口。
- **怎么滚**：内存 deque(maxlen=5000) 是唯一事实来源；启动时把文件里已有的
  尾巴读回来接着用。落盘是**追加**，文件超阈值（5000+500）才整体重写一次，
  平时就是几行的 append，不构成接收路径上的开销。
- **为什么不直接每帧写盘**：接收路径上的任何 IO 都可能撞上磁盘抖动
  （杀毒扫描、机械盘），把收包线程卡出毛刺。这里只在内存里入队，
  由 1 秒定时器批量刷盘 —— 接收性能零影响，最多丢最后一秒的尾巴（可接受）。
- **谁在用**：main_window._append_display（显示帧的唯一入口，暂停恢复的
  批量刷帧也走它）；ui_settings 的「接收历史」行打开记录文件。
"""

from __future__ import annotations

import os
from collections import deque
from pathlib import Path

MAX_LINES = 5000
COMPACT_EXTRA = 500      # 文件比上限多出这么多行才整体重写一次
FLUSH_MS = 1000          # 批量刷盘周期
FILE_NAME = "接收历史.log"

_lines: deque[str] = deque(maxlen=MAX_LINES)
_pending: list[str] = []
_file_lines = 0          # 文件当前有多少行（内存记账，免得反复数文件）
_path: Path | None = None
_timer = None            # QTimer，惰性创建（要等 QApplication 存在）


def _resolve_dir() -> Path:
    """与日志同目录：直接取日志文件的父目录；日志没起来就退回同一套候选。"""
    import app_log
    log_path = app_log.path()
    if log_path:
        return Path(log_path).parent
    return Path.home() / ".serialassistant" / "logs"


def path() -> str | None:
    return str(_path) if _path else None


def load() -> str | None:
    """启动时调用：定位文件、把已有尾巴读回内存。返回文件路径（可能没建成）。"""
    global _path, _file_lines, _timer
    directory = _resolve_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        _path = directory / FILE_NAME
    except OSError:
        _path = None
        return None
    try:
        if _path.exists():
            text = _path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines()[-MAX_LINES:]:
                _lines.append(line)
    except OSError:
        pass                    # 读不回来就当空档开始，文件照常续写
    _file_lines = len(_lines)
    if _timer is None:
        from PySide6.QtCore import QTimer
        _timer = QTimer()
        _timer.setInterval(FLUSH_MS)
        _timer.timeout.connect(flush)
        _timer.start()
    return str(_path)


def feed(display_text: str) -> None:
    """把接收区实际显示的一帧（可多行）记进历史。

    只做内存入队（deque 满了自动挤掉最老的），O(帧行数) 且无 IO ——
    接收路径上开销可忽略。空行不记（显示里的轮间空行是排版用的）。
    """
    if not display_text:
        return
    got = [ln for ln in display_text.split("\n") if ln.strip()]
    if not got:
        return
    _lines.extend(got)
    _pending.extend(got)


def flush() -> None:
    """把积攒的行批量追加进文件；超阈值时整体重写收回到 MAX_LINES。"""
    global _file_lines
    if not _pending or _path is None:
        return
    batch, _pending[:] = _pending[:], []
    try:
        with open(_path, "a", encoding="utf-8") as f:
            f.write("\n".join(batch) + "\n")
        _file_lines += len(batch)
        if _file_lines > MAX_LINES + COMPACT_EXTRA:
            _compact()
    except OSError:
        pass                    # 文件系统抖动静默降级，绝不影响接收


def _compact() -> None:
    """整体重写：只留内存 deque 里的最近 MAX_LINES 行（临时文件 + 原子替换）。"""
    global _file_lines
    tmp = _path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")
    os.replace(tmp, _path)
    _file_lines = len(_lines)


def lines() -> tuple[str, ...]:
    """当前内存里的历史行（自测用）。"""
    return tuple(_lines)
