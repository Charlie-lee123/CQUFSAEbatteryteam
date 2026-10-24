# -*- coding: utf-8 -*-
r"""蓝牙模式往返切换的**稳定性**探针（6 个来回）。

为什么要单独跑这一条：整轮冒烟里出现过两次"进程硬退"（exit 127、无 Python
traceback、无 faulthandler 输出、事件查看器也没有 Application Error），
两次都停在「蓝牙广播 → 切回串口」这一段附近。这个探针把那段摘出来重复跑，
用来区分两种情况：

  · 每次都死在同一个点上  → 产品侧（ble_io / WinRT 线程收尾）有真问题；
  · 跑完 6 个来回没事     → 那次是外部因素（被打断），不是产品崩溃。

用法：QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_probe_smoke_ble_loop.py
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import QSettings, QEvent                      # noqa: E402
from PySide6.QtWidgets import QApplication                        # noqa: E402

CYCLES = int(os.environ.get("BLE_CYCLES", "6"))


def main() -> int:
    import faulthandler
    crash = open(os.path.join(_ROOT, "_check", "ble_loop_crash.txt"), "w",
                 encoding="utf-8")
    faulthandler.enable(file=crash, all_threads=True)

    app = QApplication(sys.argv)
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    app.setStyle("Fusion")
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))

    import rx_history
    import tempfile
    from pathlib import Path
    real_resolve = rx_history._resolve_dir
    rx_history._resolve_dir = lambda: Path(tempfile.mkdtemp(prefix="bleloop_hist_"))

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    from main_window import LINK_BLE, LINK_SERIAL, MainWindow
    MainWindow._auto_check_update = lambda self: None
    window = MainWindow()
    window._port_watch.stop()
    window.cmb_link.setCurrentText(LINK_SERIAL)
    window.show()
    window.setGeometry(120, 80, 1100, 780)

    def pump(ms: int) -> None:
        end = time.perf_counter() + ms / 1000.0
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.002)

    pump(500)
    for index in range(1, CYCLES + 1):
        print(f"[{index}/{CYCLES}] 切到蓝牙 …", flush=True)
        window.cmb_link.setCurrentText(LINK_BLE)
        pump(300)
        deadline = time.perf_counter() + 12.0
        while time.perf_counter() < deadline and window._ble_scanner is not None:
            app.processEvents()
            time.sleep(0.01)
        print(f"[{index}/{CYCLES}] 扫描结束 scanner={window._ble_scanner!r}"
              f" 列表={window.cmb_port.count()} 项"
              f" 线程数={__import__('threading').active_count()}", flush=True)
        window.cmb_port.showPopup()
        pump(250)
        window.cmb_port.hidePopup()
        pump(150)
        print(f"[{index}/{CYCLES}] 切回串口 …", flush=True)
        window.cmb_link.setCurrentText(LINK_SERIAL)
        pump(400)
        print(f"[{index}/{CYCLES}] OK 状态={window.lbl_state.text()!r}", flush=True)

    print("[完成] 6 个来回全部跑完，没有硬退", flush=True)

    window.close()
    window.deleteLater()
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    rx_history._path = None
    rx_history._lines.clear()
    rx_history._resolve_dir = real_resolve
    for key in settings.allKeys():
        if key not in snapshot:
            settings.remove(key)
    for key, value in snapshot.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)
    settings.sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
