# -*- coding: utf-8 -*-
"""复现「改了设置之后，最大化的窗口四周出现缝隙」。

用户报的现象：全屏（我们说的"最大化"就是整窗全屏）状态下打开设置面板、点确定，
窗口四周就漏出桌面/壁纸 —— 也就是 shell 不再把它当全屏应用，用"最大化"的排版
把它重排了一遍。这个探针量窗口物理矩形与显示器矩形的差。
"""
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

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
from ui_theme import apply_scheme

apply_scheme("dark")
from main_window import MainWindow

user32 = ctypes.windll.user32
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]


def rect_of(hwnd):
    """窗口的**物理**矩形（进程已声明 DPI 感知，拿到的就是物理像素）。"""
    r = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def monitor_rect(hwnd=None):
    """所在显示器的**物理**矩形。

    走 Qt 而不是 GetMonitorInfoW：后者的 ctypes 原型会被 wintypes 里同名的
    MONITORINFO 干扰（`expected LP_MONITORINFO instance`），Qt 这边
    `geometry()` 给逻辑坐标、乘上屏幕的 devicePixelRatio 就是物理像素。
    """
    from PySide6.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen()
    g = screen.geometry()
    dpr = screen.devicePixelRatio()
    return (int(g.x() * dpr), int(g.y() * dpr),
            int((g.x() + g.width()) * dpr), int((g.y() + g.height()) * dpr))


def corner_pref(hwnd):
    """读 DWM 的窗口圆角偏好：1 = DONOTROUND（方角）、2 = ROUND（圆角）。

    全屏窗口必须是 1 —— 圆角是在窗口**内侧**切掉四块，全屏时四角就会露出桌面。
    """
    value = ctypes.c_int(-1)
    dwmapi = ctypes.windll.dwmapi
    dwmapi.DwmGetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                             ctypes.c_void_p, ctypes.c_uint]
    dwmapi.DwmGetWindowAttribute(ctypes.c_void_p(hwnd), 33,
                                 ctypes.byref(value), ctypes.sizeof(value))
    return value.value


def report(label, hwnd):
    win = rect_of(hwnd)
    mon = monitor_rect(hwnd)
    gap = (win[0] - mon[0], win[1] - mon[1], mon[2] - win[2], mon[3] - win[3])
    corner = corner_pref(hwnd)
    ok = all(g == 0 for g in gap) and corner == 1
    print(f"{label}: 窗口 {win} / 显示器 {mon} / 四边露出 {gap} / "
          f"圆角 {corner}{'（方角）' if corner == 1 else '（圆角！四角漏桌面）'}  "
          f"{'OK' if ok else '<<< 有缝隙'}")
    return gap, corner


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


settings = QSettings("MimoWorks", "SerialAssistant")
snapshot = {key: settings.value(key) for key in settings.allKeys()}
geometry = settings.value("geometry")
settings.remove("geometry")
settings.sync()

window = MainWindow()
window._port_watch.stop()
window.resize(1100, 820)
window.show()
pump(0.8)
window.startup_fullscreen()
pump(1.4)
hwnd = int(window.winId())
before = report("① 全屏之后", hwnd)


def accept_settings():
    dialog = QApplication.activeModalWidget()
    print("  对话框 =", dialog)
    if dialog is None:
        return
    try:
        # 改一项 + **换主题**：换主题会重走 apply_titlebar_theme —— 那一步原来
        # 无条件把窗口圆角设回 ROUND，全屏窗口的四角就是这么漏出桌面的。
        dialog._custom_widgets["pause_snap_lines"].setValue(
            dialog._custom_widgets["pause_snap_lines"].value() + 1)
        dialog.cmb_theme.setCurrentIndex(1 if dialog.cmb_theme.currentIndex() != 1 else 2)
        print("  改项 + 换主题 ->", dialog.cmb_theme.currentText())
    except Exception as exc:               # noqa: BLE001
        print("  改设置失败:", exc)
    dialog.accept()


QTimer.singleShot(800, accept_settings)
window._open_settings()                    # 阻塞直到对话框关闭
pump(2.0)
after = report("② 设置确定之后（含换主题）", hwnd)
window.grab().save(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "_check", "gap_after_settings.png"))
print("截图 -> _check/gap_after_settings.png")

# 再直接连换两次主题（不经过对话框），单独压一压 apply_titlebar_theme 那条路
window._apply_theme("dark")
pump(0.8)
report("③ 直接 _apply_theme(dark)", hwnd)
window._apply_theme("light")
pump(0.8)
report("④ 直接 _apply_theme(light)", hwnd)
window._apply_theme("dark")
pump(0.8)
after3 = report("⑤ 再切回 dark", hwnd)

window.close()
if geometry is not None:
    settings.setValue("geometry", geometry)
for key in settings.allKeys():
    if key not in snapshot:
        settings.remove(key)
for key, value in snapshot.items():
    settings.setValue(key, value)
settings.sync()

bad = [r for r in (before, after, after3) if any(g != 0 for g in r[0]) or r[1] != 1]
print("复现成功（有缝隙/圆角）" if bad else "没有复现")
