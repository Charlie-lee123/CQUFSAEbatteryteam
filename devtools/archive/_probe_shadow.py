"""临时探针：无边框窗口能否补回 DWM 阴影，以及补阴影后全屏还藏不藏任务栏。

DWM 的影子只给"有边框"(WS_THICKFRAME) 的窗口画，无边框窗口得靠
``DwmExtendFrameIntoClientArea`` 把 1px 边框"借"进客户区 —— 这是无边框应用
拿回系统阴影/圆角的常规做法。风险是那 1px 会让 shell 不再把全屏窗口认成
"全屏应用"，任务栏就藏不住了 —— 所以必须实测底部像素。
"""

import ctypes
import os
import sys

# 脚本挪进 devtools/ 之后，运行时项目根不再自动在 sys.path 里 —— 补上
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import time
from ctypes import wintypes

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

from main_window import MainWindow
from ui_theme import apply_scheme

apply_scheme("dark")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_check")

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi


class MARGINS(ctypes.Structure):
    _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]


def set_frame_into_client(hwnd: int, px: int) -> int:
    margins = MARGINS(px, px, px, px)
    return dwmapi.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(margins))


def bottom_color() -> tuple:
    image = QGuiApplication.primaryScreen().grabWindow(0).toImage()
    c = image.pixelColor(image.width() // 2, image.height() - 3)
    return (c.red(), c.green(), c.blue())


def corner_color() -> tuple:
    image = QGuiApplication.primaryScreen().grabWindow(0).toImage()
    c = image.pixelColor(2, 2)
    return (c.red(), c.green(), c.blue())


def pump(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


settings = QSettings("MimoWorks", "SerialAssistant")
snapshot = {k: settings.value(k) for k in settings.allKeys()}

window = MainWindow()
window._port_watch.stop()
window.show()
pump(0.4)
window._on_escape()
window.setGeometry(260, 160, 1000, 760)
window.raise_()
window.activateWindow()
pump(1.0)
hwnd = int(window.winId())

print("== 不加 DWM 边框 ==")
window.raise_()
pump(0.6)
QGuiApplication.primaryScreen().grabWindow(0).save(
    os.path.join(OUT, "shadow_none.png"))

print("== 加 DwmExtendFrameIntoClientArea(1px) ==")
print("  返回:", set_frame_into_client(hwnd, 1))
window.raise_()
pump(1.0)
QGuiApplication.primaryScreen().grabWindow(0).save(
    os.path.join(OUT, "shadow_on.png"))

print("== 加了阴影之后进全屏，任务栏还在吗 ==")
window.startup_fullscreen()
pump(1.5)
print("  isFullScreen:", window.isFullScreen(), "底部像素:", bottom_color(),
      "左上角像素:", corner_color())
QGuiApplication.primaryScreen().grabWindow(0).save(
    os.path.join(OUT, "shadow_fullscreen.png"))

print("== 全屏时把边框还回 0px，再看底部 ==")
set_frame_into_client(hwnd, 0)
window._on_escape()
pump(0.5)
window.startup_fullscreen()
pump(1.5)
print("  isFullScreen:", window.isFullScreen(), "底部像素:", bottom_color())

window.close()
for key in settings.allKeys():
    if key not in snapshot:
        settings.remove(key)
for key, value in snapshot.items():
    settings.setValue(key, value)
settings.sync()
