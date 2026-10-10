# -*- coding: utf-8 -*-
"""量「接收区展开 → 收起」前后的窗口几何：定位那 1,17 的偏移从哪来。"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

# 脚本挪进 devtools/ 之后，运行时项目根不再自动在 sys.path 里 —— 补上
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()
ctypes.windll.user32.SetProcessDPIAware()

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
from ui_theme import apply_scheme

apply_scheme("dark")
from main_window import MainWindow

user32 = ctypes.windll.user32
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]


def win_rect(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


settings = QSettings("MimoWorks", "SerialAssistant")
snapshot = {key: settings.value(key) for key in settings.allKeys()}

window = MainWindow()
window._port_watch.stop()
window.show()
pump(0.6)
print("① 刚 show: geometry =", window.geometry().getRect())
print("   窗口 rect(物理) =", win_rect(int(window.winId())))

window.resize(700, 600)
pump(0.4)
pre = window.geometry().getRect()
print("② resize(700,600) 后: geometry =", pre, "(断言里的 pre_geom)")
print("   窗口 rect(物理) =", win_rect(int(window.winId())))

window.btn_expand.click()
pump(1.4)
print("③ 展开(全屏): geometry =", window.geometry().getRect(),
      "isFullScreen =", window.isFullScreen())

window.btn_expand.click()
pump(1.4)
after = window.geometry().getRect()
print("④ 收起后: geometry =", after, " 差 =", tuple(b - a for a, b in zip(pre, after)))
print("   窗口 rect(物理) =", win_rect(int(window.winId())))

window.close()
for key in settings.allKeys():
    if key not in snapshot:
        settings.remove(key)
for key, value in snapshot.items():
    settings.setValue(key, value)
settings.sync()
