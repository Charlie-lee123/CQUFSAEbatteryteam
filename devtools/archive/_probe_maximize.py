"""临时探针：用**系统级**最大化（WM_SYSCOMMAND / SC_MAXIMIZE，等价于真点标题栏方块）
复现"最大化之后四周还有缝隙"。
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

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

from main_window import MainWindow

settings = QSettings("MimoWorks", "SerialAssistant")
snapshot = {k: settings.value(k) for k in settings.allKeys()}

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_check")
os.makedirs(OUT, exist_ok=True)

user32 = ctypes.windll.user32
WM_SYSCOMMAND, SC_MAXIMIZE = 0x0112, 0xF030


def phys_rect(hwnd):
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    r = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def monitor_rect(hwnd):
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", ctypes.c_ulong)]

    user32.MonitorFromWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    m = user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(m, ctypes.byref(info))
    rc, rw = info.rcMonitor, info.rcWork
    return (rc.left, rc.top, rc.right, rc.bottom), (rw.left, rw.top, rw.right, rw.bottom)


def shot(tag):
    pix = QGuiApplication.primaryScreen().grabWindow(0)
    path = os.path.join(OUT, f"sysmax_{tag}.png")
    pix.save(path)
    return pix


def report(tag, w, capture=False):
    hwnd = int(w.winId())
    mon, work = monitor_rect(hwnd)
    r = phys_rect(hwnd)
    st = w.windowState()
    flags = []
    if st & Qt.WindowState.WindowFullScreen:
        flags.append("FullScreen")
    if st & Qt.WindowState.WindowMaximized:
        flags.append("Maximized")
    line = (f"[{tag}] state={'|'.join(flags) or 'Normal'} "
            f"isFullScreen={w.isFullScreen()} win={r} mon={mon} "
            f"gap L/T/R/B={r[0]-mon[0]}/{r[1]-mon[1]}/{mon[2]-r[2]}/{mon[3]-r[3]}")
    print(line)
    if capture:
        pix = shot(tag)
        img = pix.toImage()
        for name, (x, y) in {"左上": (0, 0), "右下": (img.width()-1, img.height()-1),
                             "上中": (img.width()//2, 0),
                             "下中": (img.width()//2, img.height()-1)}.items():
            c = img.pixelColor(x, y)
            print(f"      边缘{name} = {c.red()},{c.green()},{c.blue()}")
    return line


w = MainWindow()
w._port_watch.stop()
w.show()
app.processEvents()


def step_startup():
    w.startup_fullscreen()
    QTimer.singleShot(800, lambda: (report("0 startup", w, True),
                                    QTimer.singleShot(300, step_normal)))


def step_normal():
    w._expect_normal = True
    w.showNormal()
    w._expect_normal = False
    QTimer.singleShot(600, lambda: (report("1 normal", w),
                                    QTimer.singleShot(300, sys_maximize)))


def sys_maximize():
    hwnd = int(w.winId())
    user32.SendMessageW(wintypes.HWND(hwnd), WM_SYSCOMMAND, SC_MAXIMIZE, 0)
    QTimer.singleShot(1200, lambda: (report("2 sys-maximize", w, True),
                                     QTimer.singleShot(300, sys_maximize2)))


def sys_maximize2():
    w._on_escape()
    QTimer.singleShot(600, lambda: (report("3 after Esc", w),
                                    QTimer.singleShot(300, do_max2)))


def do_max2():
    hwnd = int(w.winId())
    user32.SendMessageW(wintypes.HWND(hwnd), WM_SYSCOMMAND, SC_MAXIMIZE, 0)
    QTimer.singleShot(1200, lambda: (report("4 sys-max 2nd", w, True), done()))


def done():
    w.close()
    for key in settings.allKeys():
        if key not in snapshot:
            settings.remove(key)
    for key, value in snapshot.items():
        settings.setValue(key, value)
    settings.sync()
    app.quit()


QTimer.singleShot(400, step_startup)
app.exec()
