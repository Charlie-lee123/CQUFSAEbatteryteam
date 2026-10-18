# -*- coding: utf-8 -*-
r"""复现「恢复后没有追底」：把 2b 用例的步骤逐条打出来（暂停/恢复前后的滚动条值）。"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import QSettings                             # noqa: E402
from PySide6.QtWidgets import QApplication                        # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    from main_window import MainWindow
    window = MainWindow()
    bar = None
    try:
        window.show()
        window.apply_startup_state()
        for _ in range(120):
            app.processEvents()
            time.sleep(0.003)
        bar = window.txt_rx.verticalScrollBar()
        window._on_rx_lines_changed(30)
        window._clear_receive()
        for i in range(60):
            window._emit_block(f"bulk {i:03d} " + "q" * 50)
        app.processEvents()
        print(f"[before pause] v={bar.value()} max={bar.maximum()} vp={window.txt_rx.viewport().height()}")
        window._toggle_pause()
        print(f"[pause]        v={bar.value()} max={bar.maximum()} vp={window.txt_rx.viewport().height()}")
        for i in range(40):
            window._emit_block(f"drift {i:03d} " + "z" * 50)
            app.processEvents()
        print(f"[paused end]   v={bar.value()} max={bar.maximum()} vp={window.txt_rx.viewport().height()}")
        window._toggle_pause()
        print(f"[resume t0]    v={bar.value()} max={bar.maximum()} vp={window.txt_rx.viewport().height()}")
        app.processEvents()
        print(f"[resume +pe]   v={bar.value()} max={bar.maximum()} vp={window.txt_rx.viewport().height()}")
        for _ in range(60):
            app.processEvents()
            time.sleep(0.004)
        print(f"[resume +240]  v={bar.value()} max={bar.maximum()} vp={window.txt_rx.viewport().height()}")
        for _ in range(60):
            app.processEvents()
            time.sleep(0.004)
        print(f"[resume +480]  v={bar.value()} max={bar.maximum()} vp={window.txt_rx.viewport().height()}")
        window._on_rx_lines_changed(window._rx_blocks)
        window._clear_receive()
    finally:
        window.setGeometry(0, 0, 1134, 820)
        window.close()
        app.processEvents()
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            if value is None:
                settings.remove(key)
            else:
                settings.setValue(key, value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
