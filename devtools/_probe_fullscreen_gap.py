# -*- coding: utf-8 -*-
"""探针：全屏 → 打开设置 → 确定保存 之后，窗口四角是否圆了（漏桌面）。

模拟的是 `_open_settings` 里 dialog.exec() 返回 Accepted 之后那段真实代码。
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
    value = ctypes.c_int(-1)
    ctypes.windll.dwmapi.DwmGetWindowAttribute(
        ctypes.c_void_p(hwnd), 33, ctypes.byref(value), ctypes.sizeof(value))
    return value.value


def window_rect(hwnd):
    import ctypes.wintypes as wt
    r = wt.RECT()
    ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def main() -> None:
    from main_window import MainWindow
    from ui_settings import SettingsDialog
    import main_window as mw

    app = QApplication.instance() or QApplication(sys.argv)
    s = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: s.value(k) for k in s.allKeys()}
    window = MainWindow()
    try:
        # 完整复刻真实启动序列（main.py）：show() → apply_startup_state()
        window.show()
        app.processEvents()
        window.apply_startup_state()
        for _ in range(80):
            app.processEvents()
        hwnd = int(window.winId())
        print(f"启动全屏: fullscreen={window.isFullScreen()} "
              f"圆角={corner_pref(hwnd)}(1=方角) rect={window_rect(hwnd)}")

        # 场景 A：接收区展开态（用户说的"最大化后的软件界面"）
        window._expand_receive()
        for _ in range(60):
            app.processEvents()
        print(f"展开态  : expanded={window._expanded} fullscreen={window.isFullScreen()} "
              f"圆角={corner_pref(hwnd)} rect={window_rect(hwnd)}")

        # === 完整复刻 _open_settings 的"确定"分支（含真实模态 exec）===
        dialog = SettingsDialog(window._theme_mode, 0,
                               mw.BUTTON_SPECS, sorted(window._hidden_buttons),
                               on_glass=window._apply_glass,
                               on_buttons=window._preview_buttons, parent=window)
        # 真实模态：用定时器在对话框起来后点"确定"，走 exec() 全套
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, dialog.accept)
        dialog.exec()
        print(f"对话框关闭后: fullscreen={window.isFullScreen()} "
              f"圆角={corner_pref(hwnd)}(1=方角)")
        theme_mode, glass = dialog.values()
        window._theme_mode = theme_mode
        window._hidden_buttons = set(dialog.hidden_buttons())
        window._apply_button_visibility()
        window._settings.setValue("theme_mode", theme_mode)
        window._apply_glass(glass)
        if theme_mode == "system":
            from ui_theme import detect_system_scheme
            window._apply_theme(detect_system_scheme())
        else:
            window._apply_theme(theme_mode)
        dialog.close()
        dialog.deleteLater()
        for _ in range(60):
            app.processEvents()
        print(f"保存后  : fullscreen={window.isFullScreen()} "
              f"圆角={corner_pref(hwnd)}(1=方角) rect={window_rect(hwnd)}")
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
