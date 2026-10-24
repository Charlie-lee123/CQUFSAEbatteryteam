# -*- coding: utf-8 -*-
"""历史接收数据：自动保存最近的接收内容（含时间戳行）。

- **存哪**：与日志文件同一目录（``app_log`` 的 logs 目录，跟着软件走）。
- **存什么**：接收区实际显示的行——已含时间戳行、段重排后的数据行；
  HEX 视图存的就是 HEX 文本。逐帧喂 ``feed()``，尾部换行由这里统一收口。
- **存多少**：**跟随接收区的「保留行数」**（用户 2026-10-13 点名："本地留存行数
  数量与接收区设置的行数保持一致"）。接收区改了行数会调 ``set_max_lines()``
  同步。默认值与接收区默认一致（5000）。
- **怎么滚**：内存 deque 是唯一事实来源；启动时把文件里已有的尾巴读回来接着用。
  落盘是**追加**，文件超阈值（上限 + COMPACT_EXTRA）才整体重写一次，平时就是
  几行的 append，不构成接收路径上的开销。
- **为什么不直接每帧写盘**：接收路径上的任何 IO 都可能撞上磁盘抖动
  （杀毒扫描、机械盘），把收包线程卡出毛刺。这里只在内存里入队，
  由 1 秒定时器批量刷盘 —— 接收性能零影响，最多丢最后一秒的尾巴（可接受）。
- **谁在用**：main_window._append_display（显示帧的唯一入口，暂停恢复的
  批量刷帧也走它）与 _on_rx_lines_changed（同步行数）；ui_settings 的
  「接收历史」行打开记录文件；main_window 的「打开历史数据」按钮回放。
"""

from __future__ import annotations

import os
import re
from collections import deque
from pathlib import Path

DEFAULT_MAX_LINES = 5000
COMPACT_EXTRA = 500      # 文件比上限多出这么多行才整体重写一次
FLUSH_MS = 1000          # 批量刷盘周期
FILE_NAME = "接收历史.log"

_max_lines = DEFAULT_MAX_LINES
_lines: deque[str] = deque(maxlen=_max_lines)
_pending: list[str] = []
_file_lines = 0          # 文件当前有多少行（内存记账，免得反复数文件）
_path: Path | None = None
_timer = None            # QTimer，惰性创建（要等 QApplication 存在）


def max_lines() -> int:
    """当前留存上限（行）。"""
    return _max_lines


def set_max_lines(value: int) -> None:
    """把留存上限改成 ``value`` 行（跟随接收区「保留行数」）。

    deque 的 maxlen 不能原地改，换一个新 deque 并把尾巴搬过去（旧的那头自然
    丢掉）；已经在文件里的多余行标脏，下一次 compact 收回来。
    """
    global _max_lines, _lines, _file_lines
    value = max(1, int(value))
    if value == _max_lines:
        return
    # 先取旧行、再换 deque：不能写成 deque(_lines, maxlen=value) 之外的
    # "复用 _lines 自身"写法，否则 maxlen 取的是旧值（实测踩过）。
    kept = list(_lines)[-value:]
    _max_lines = value
    _lines = deque(kept, maxlen=value)
    # 上限调小后文件里可能还有多余行：标脏，下次 flush 必触发一次 compact
    if _file_lines > value + COMPACT_EXTRA:
        _file_lines = value + COMPACT_EXTRA + 1
    return None


def _resolve_dir() -> Path:
    """与日志同目录：直接取日志文件的父目录；日志没起来就退回同一套候选。"""
    import app_log
    log_path = app_log.path()
    if log_path:
        return Path(log_path).parent
    return Path.home() / ".serialassistant" / "logs"


def path() -> str | None:
    return str(_path) if _path else None


def _safe_key(key: str | None) -> str:
    """车辆名 → 文件名安全段（非法字符换成 _，限长 80）。"""
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(key or "").strip())
    return text.strip(" .")[:80]


def file_for(key: str | None) -> str:
    """车辆名 → 历史文件名；key 为空 = 未选车，落到通用文件。"""
    safe = _safe_key(key)
    return f"接收历史-{safe}.log" if safe else FILE_NAME


def switch_target(key: str | None) -> str | None:
    """把历史记录**切到某台车**（key=None = 未选车，用通用文件）。

    不同电池箱的历史数据**分开放**（用户 2026-10-23 要求）：每台车一个
    「接收历史-<车名>.log」，切车前先把当前文件的尾巴落盘，内存里换上新
    文件的最近 _max_lines 行。删车**不删文件** —— 已接收的数据不受影响。
    """
    flush()
    return load(key)


def load(key: str | None = None) -> str | None:
    """启动/切车时调用：定位文件、把已有尾巴读回内存。返回路径（可能没建成）。

    ``key`` = 当前车辆名（"E74-2026" 这种）或 None（未选车 → 通用文件）。
    """
    global _path, _file_lines, _timer
    _pending.clear()
    _lines.clear()
    _file_lines = 0
    directory = _resolve_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        _path = directory / file_for(key)
    except OSError:
        _path = None
        return None
    try:
        if _path.exists():
            text = _path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines()[-_max_lines:]:
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

    ⛔ **空行也要记**（2026-10-24 用户点名："怎么出现了该空行的地方没有空行…
    历史数据格式得和接收时一模一样"）。接收区里轮与轮之间那一行空行是
    ``_emit_block`` 用 ``f"\\n{stamp}"`` 造出来的**真实的一行**，以前这里
    ``if ln.strip()`` 把它滤掉了 —— 于是历史里每轮的时间戳都紧贴着上一轮的
    数据，回放出来跟实时看到的不是一个样子。

    存的就是 ``display`` 这个字符串原样拆行：它已经过 ``_emit_block`` 的
    align_indexes 排版、也已含时间戳行，观感与屏上一致。只做内存入队，
    O(帧行数) 且无 IO。
    """
    if not display_text:
        return
    got = display_text.split("\n")
    if not any(line.strip() for line in got):
        return                       # 整帧都是空行：不值得占位
    _lines.extend(got)
    _pending.extend(got)


def flush() -> None:
    """把积攒的行批量追加进文件；超阈值时整体重写收回到上限。

    ``_file_lines`` 被 ``set_max_lines`` 标脏（> 上限+COMPACT_EXTRA）时，
    即使本轮没有待写行也要走一次 compact —— 否则用户把保留行数调小之后，
    文件会一直停在旧的大行数上，跟接收区对不上（用户点名要求一致）。
    """
    global _file_lines
    if _path is None:
        # ⛔ 历史目录建不出来时 load() 会 early-return（_path 保持 None），但
        #    _emit_block 仍然每帧 feed()。_pending 是普通 list、**没有上限**，
        #    不清就会随接收时长一路涨（_lines 有 maxlen 兜住，_pending 没有）。
        _pending.clear()
        return
    dirty = _file_lines > _max_lines + COMPACT_EXTRA
    if not _pending:
        if dirty:
            _compact()
        return
    batch, _pending[:] = _pending[:], []
    try:
        with open(_path, "a", encoding="utf-8") as f:
            f.write("\n".join(batch) + "\n")
        _file_lines += len(batch)
    except OSError:
        return                  # 文件系统抖动静默降级，绝不影响接收
    if _file_lines > _max_lines + COMPACT_EXTRA:
        _compact()


def _compact() -> None:
    """整体重写：只留内存 deque 里的最近 _max_lines 行（临时文件 + 原子替换）。"""
    global _file_lines
    tmp = _path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines) + "\n")
        os.replace(tmp, _path)
    except OSError:
        return                  # 重写失败就维持原文件，下次 flush 再试
    _file_lines = len(_lines)


def lines() -> tuple[str, ...]:
    """当前内存里的历史行（自测用）。"""
    return tuple(_lines)


def _timer_stop() -> None:
    """自测用：暂停定时刷盘，免得测试期间把临时文件写脏或撞上真实文件。"""
    if _timer is not None:
        _timer.stop()


def _resume_timer() -> None:
    """自测用：恢复定时刷盘。"""
    if _timer is not None:
        _timer.start()


def all_lines() -> tuple[str, ...]:
    """回放用：优先读整个文件（可能比内存 deque 更长/更全）。

    读不到就退回内存副本。文件里的行才是"本地留存"的真实快照。
    """
    if _path is not None:
        try:
            if _path.exists():
                text = _path.read_text(encoding="utf-8", errors="replace")
                return tuple(text.splitlines())
        except OSError:
            pass
    return tuple(_lines)
