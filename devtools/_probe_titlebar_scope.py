# -*- coding: utf-8 -*-
"""探针：给设置对话框设标题栏主题，不能波及主窗口的圆角。

REVERSE 标记：只读，可在主工作区安全运行。
"""
import ctypes
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


def corner_pref(hwnd):
    v = ctypes.c_int(-1)
    ctypes.windll.dwmapi.DwmGetWindowAttribute(
        ctypes.c_void_p(hwnd), 33, ctypes.byref(v), ctypes.sizeof(v))
    return v.value


def main():
    from main_window import MainWindow
    from ui_settings import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    s = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: s.value(k) for k in s.allKeys()}
    window = MainWindow()
    try:
        window.show()
        app.processEvents()
        window.apply_startup_state()
        for _ in range(60):
            app.processEvents()
        main_hwnd = int(window.winId())
        print(f"全屏主窗口: 圆角={corner_pref(main_hwnd)} (1=方角 正确)")

        dlg = SettingsDialog(window._theme_mode, 0, parent=window)
        dlg.show()
        app.processEvents()
        dlg_hwnd = int(dlg.winId())
        print(f"对话框开启后主窗口: 圆角={corner_pref(main_hwnd)} (必须仍为 1)")
        print(f"对话框自身: 圆角={corner_pref(dlg_hwnd)} (可为 2=圆角，不影响主窗口)")
        dlg.close()
        app.processEvents()
        print(f"对话框关闭后主窗口: 圆角={corner_pref(main_hwnd)} (必须仍为 1)")
    finally:
        window.close()
        app.processEvents()
        for k in s.allKeys():
            if k not in snap:
                s.remove(k)
        for k, v in snap.items():
            s.setValue(k, v)
        s.sync()


if __name__ == "__main__":
    main()
