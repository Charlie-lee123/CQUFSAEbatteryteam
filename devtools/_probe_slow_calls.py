# -*- coding: utf-8 -*-
r"""抓「哪次 Python 调用超过 40ms」——真流程里定位开屏期长堵点的最终判决。

前三次测量已经把范围逼到很窄：
  - `_probe_combo_ctor.py`：`ChevronComboBox()` 只要 0.2ms（不是下拉框）
  - `_probe_splash_cprofile.py`：真流程 `MainWindow()` 共 358ms，最大单项 `winId` 68ms
  - `_probe_build_pumps.py`：`_build_ui` 里的 9 个让出点间隔都 ≤33ms
⇒ 剩下的 259ms 只可能落在**没有让出点的那几个 builder** 里（`_build_config_panel`
   / `_build_receive_area` / `_build_send_area`），且是多次小操作累计。

本探针用 `sys.setprofile` 的 call/return 事件给**每一次 Python 调用计时**，
只打印耗时 > 40ms 的那些（函数名 + 耗时 + 是否首帧）。setprofile 有开销
（绝对毫秒偏大），但足够判定「是哪一段」。

⛔ 只读探针：不改产品源码、不打包、不写 QSettings。
"""
from __future__ import annotations

import os
import sys
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RUN_MS = 7000
SLOW_MS = 40.0
_slow: list[tuple[float, str, int]] = []      # (ms, name, depth)
_stack: list[tuple[str, float, int]] = []
_armed = [False]


def _prof(frame, event, _arg):
    if not _armed[0]:
        return
    co = frame.f_code
    if event == "call":
        _stack.append((f"{os.path.basename(co.co_filename)}:{co.co_firstlineno}"
                       f" {co.co_name}", time.perf_counter(), len(_stack)))
        return
    if not _stack:
        return
    name, t0, depth = _stack.pop()
    ms = (time.perf_counter() - t0) * 1000.0
    if ms >= SLOW_MS:
        _slow.append((ms, name, depth))


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    import main_window

    _orig = main_window.MainWindow.__init__

    def _wrapped(self, *a, **kw):
        _stack.clear()
        sys.setprofile(_prof)
        _armed[0] = True
        t0 = time.perf_counter()
        try:
            _orig(self, *a, **kw)
        finally:
            _armed[0] = False
            sys.setprofile(None)
            print(f"\n=== MainWindow() 构造 = {(time.perf_counter() - t0) * 1000:.0f} ms"
                  f"（含 setprofile 开销）===", flush=True)

    main_window.MainWindow.__init__ = _wrapped

    import main as app_main

    _orig_exec = QApplication.exec
    QApplication.exec = lambda *a: (QTimer.singleShot(
        RUN_MS, QApplication.instance().quit), _orig_exec())

    try:
        app_main.main()
    except SystemExit:
        pass
    except Exception:                      # noqa: BLE001
        traceback.print_exc()

    print(f"\n=== 单次调用 > {SLOW_MS:.0f}ms 的共 {len(_slow)} 处（降序）===")
    for ms, name, depth in sorted(_slow, key=lambda x: -x[0]):
        print(f"  {ms:7.1f} ms  {'  ' * depth}{name}")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
