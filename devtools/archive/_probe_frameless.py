"""临时探针：无边框窗口的样式位、全屏是否仍能藏住任务栏、caption 字形渲染。

三件事都必须先量，再决定实现方式：
1. `FramelessWindowHint` 在 Windows 上到底去掉了哪些样式位（WS_CAPTION / WS_THICKFRAME）？
   没有 WS_THICKFRAME 就没有原生缩放边框和 DWM 阴影，得自己接 `startSystemResize`。
2. 无边框之后 `showFullScreen` 还能不能让 shell 藏任务栏？—— 这是用户"最大化盖住
   任务栏"那条需求的命门，量一下屏幕底部像素就知道。
3. 三个 caption 字形（最小化 / 最大化 / 还原 / 关闭）渲染出来到底长什么样：
   本项目规矩是"码位逐个渲染看过再定"。
"""

import ctypes
import os
import sys

# 脚本挪进 devtools/ 之后，运行时项目根不再自动在 sys.path 里 —— 补上
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from ctypes import wintypes

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

from main_window import MainWindow
from ui_theme import apply_scheme, icon_font, qcolor

apply_scheme("dark")

user32 = ctypes.windll.user32
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
GWL_STYLE, GWL_EXSTYLE = -16, -20
BITS = {
    "WS_CAPTION(0xC00000)": 0x00C00000,
    "WS_THICKFRAME(0x40000)": 0x00040000,
    "WS_SYSMENU(0x80000)": 0x00080000,
    "WS_MINIMIZEBOX(0x20000)": 0x00020000,
    "WS_MAXIMIZEBOX(0x10000)": 0x00010000,
    "WS_POPUP(0x80000000)": 0x80000000,
    "WS_BORDER(0x800000)": 0x00800000,
}


def styles(hwnd, which=GWL_STYLE) -> dict:
    value = user32.GetWindowLongPtrW(wintypes.HWND(hwnd), which)
    return {name: bool(value & bit) for name, bit in BITS.items()}


def bottom_color() -> tuple:
    """整屏截图取最底部一行的颜色 —— 窗口盖住任务栏时应是页面底色。"""
    image = QGuiApplication.primaryScreen().grabWindow(0).toImage()
    x = image.width() // 2
    c = image.pixelColor(x, image.height() - 3)
    return (c.red(), c.green(), c.blue())


settings = QSettings("MimoWorks", "SerialAssistant")
snapshot = {k: settings.value(k) for k in settings.allKeys()}
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_check")

# ---- 1) 有边框（现状）----
framed = MainWindow()
framed._port_watch.stop()
framed.show()
app.processEvents()
print("有边框  GWL_STYLE :", styles(int(framed.winId())))
print("  底部像素(普通窗口):", bottom_color())
framed.close()
app.processEvents()

# ---- 2) 无边框 ----
win = MainWindow()
win._port_watch.stop()
win.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
win.show()
for _ in range(40):
    app.processEvents()
hwnd = int(win.winId())
print("\n无边框  GWL_STYLE :", styles(hwnd))
print("  窗口矩形:", win.geometry().getRect())

win.startup_fullscreen()
for _ in range(60):
    app.processEvents()
print("  全屏后 isFullScreen:", win.isFullScreen(), "底部像素:", bottom_color())
print("  全屏后 GWL_STYLE :", styles(hwnd))

# ---- 3) 无边框 + 手工补回 WS_THICKFRAME ----
win._on_escape()
for _ in range(40):
    app.processEvents()
print("\n无边框+THICKFRAME 前 底部像素:", bottom_color())
GWLP = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
user32.SetWindowLongPtrW(hwnd, GWL_STYLE, GWLP | 0x00040000)
win.startup_fullscreen()
for _ in range(60):
    app.processEvents()
print("  加 THICKFRAME 后全屏底部像素:", bottom_color())
print("  加 THICKFRAME 后 GWL_STYLE :", styles(hwnd))
win.close()
app.processEvents()

# ---- 4) caption 字形渲染 ----
GLYPHS = {
    "min  E921": "\uE921", "max  E922": "\uE922",
    "rest E923": "\uE923", "close E8BB": "\uE8BB",
    "close E711": "\uE711", "max  E739": "\uE739",
    "rest E73F": "\uE73F", "min  E949": "\uE949",
}
image = QImage(150 * len(GLYPHS), 90, QImage.Format.Format_ARGB32)
image.fill(0)
painter = QPainter(image)
painter.setFont(icon_font(26))
painter.setPen(qcolor("ink"))
for index, (name, glyph) in enumerate(GLYPHS.items()):
    painter.drawText(index * 150 + 20, 56, glyph)
painter.end()
path = os.path.join(OUT, "caption_glyphs.png")
image.save(path)
print("\n字形样张:", path, "顺序:", list(GLYPHS))

for key in settings.allKeys():
    if key not in snapshot:
        settings.remove(key)
for key, value in snapshot.items():
    settings.setValue(key, value)
settings.sync()
