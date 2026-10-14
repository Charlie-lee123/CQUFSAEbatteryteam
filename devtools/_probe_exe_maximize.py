# -*- coding: utf-8 -*-
"""临时探针：在**打包产物**上用**真实鼠标**点标题栏的最大化按钮，看窗口铺不铺满。

为什么非要做这一版：之前的探针走的是 `Qt showMaximized()` 和
`WM_SYSCOMMAND/SC_MAXIMIZE` 消息投递 —— 前者是 Qt 自己的状态机，后者绕过了
标题栏的命中测试。用户"点了最大化却还能看到桌面"的路径必须用真鼠标点一次才算复现。
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys

# 脚本挪进 devtools/ 之后，运行时项目根不再自动在 sys.path 里 —— 补上
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
ctypes.windll.user32.SetProcessDPIAware()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_check")
EXE = os.path.join(ROOT, "发布", "SerialAssistant.exe")
os.makedirs(OUT, exist_ok=True)

user32 = ctypes.windll.user32
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.IsZoomed.argtypes = [wt.HWND]


def monitor_rect(hwnd):
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", wt.RECT),
                    ("rcWork", wt.RECT), ("dwFlags", ctypes.c_ulong)]

    user32.MonitorFromWindow.argtypes = [wt.HWND, ctypes.c_uint]
    user32.MonitorFromWindow.restype = wt.HMONITOR
    user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]
    m = user32.MonitorFromWindow(wt.HWND(hwnd), 2)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(m, ctypes.byref(info))
    return ((info.rcMonitor.left, info.rcMonitor.top,
             info.rcMonitor.right, info.rcMonitor.bottom),
            (info.rcWork.left, info.rcWork.top,
             info.rcWork.right, info.rcWork.bottom))


def rect_of(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def report(tag, hwnd):
    mon, work = monitor_rect(hwnd)
    r = rect_of(hwnd)
    zoomed = bool(user32.IsZoomed(wt.HWND(hwnd)))
    gap = (r[0] - mon[0], r[1] - mon[1], mon[2] - r[2], mon[3] - r[3])
    print(f"[{tag}] rect={r} mon={mon} work={work} IsZoomed={zoomed} "
          f"gap(L/T/R/B)={gap}")
    return gap


def find_window():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.strip() == "CQUFSAE串口调试助手":
                found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def click(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.25)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def capture(name):
    from PySide6.QtGui import QGuiApplication
    pix = QGuiApplication.primaryScreen().grabWindow(0)
    path = os.path.join(OUT, name)
    pix.save(path)
    print("   截图:", path)


def main() -> None:
    from PySide6.QtWidgets import QApplication
    _app = QApplication(sys.argv)      # 只要 QApplication 存在（拿屏幕信息用）

    proc = subprocess.Popen([EXE])
    print("started pid", proc.pid)
    hwnd = None
    deadline = time.time() + 90
    while time.time() < deadline:
        hwnd = find_window()
        if hwnd:
            break
        time.sleep(2)
    if not hwnd:
        print("FAIL 没找到窗口")
        return
    time.sleep(6)                       # 等启动全屏稳定
    report("0 启动", hwnd)
    capture("exemax_0_startup.png")

    # 退到普通窗口（Esc），再**用真鼠标点最大化按钮**
    user32.SetForegroundWindow(wt.HWND(hwnd))
    time.sleep(0.5)
    user32.keybd_event(0x1B, 0, 0, 0)
    user32.keybd_event(0x1B, 0, 0x0002, 0)
    time.sleep(2.0)
    report("1 Esc 后普通窗口", hwnd)
    capture("exemax_1_normal.png")

    scale = 1.0
    try:
        scale = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100.0
    except Exception:
        pass
    r = rect_of(hwnd)
    # ⚠️ 标题栏现在是**自绘**的（窗口无边框，系统标题栏不存在），坐标得按我们
    # 自己的版式算：标题栏贴着窗口上沿、内容左右各留 12 逻辑 px（root margin），
    # 三颗键 46 宽、间距 2、离栏右端 6。于是各键中心的逻辑偏移（离窗口右缘）：
    #   关闭 = 12 + 6 + 23 = 41；最大化 = 41 + 46 + 2 = 89；最小化 = 137
    # y = 标题栏中心 = 34 / 2 = 17。
    for dx in (89, 90, 88, 87, 91, 86):
        x = r[2] - dx * scale
        for dy in (17 * scale, 17 * scale + 3, 17 * scale - 3):
            y = r[1] + dy
            user32.SetForegroundWindow(wt.HWND(hwnd))
            time.sleep(0.2)
            click(x, y)
            time.sleep(1.6)
            if not user32.IsWindow(wt.HWND(hwnd)):
                print(f"   点 ({x:.0f},{y:.0f}) 把窗口点没了（点到关闭键）")
                print("FAIL 探针坐标不对")
                subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"],
                               capture_output=True)
                return
            gap = report(f"2 真鼠标点 ({x:.0f},{y:.0f})", hwnd)
            if gap == (0, 0, 0, 0):
                print("   -> 铺满了，停止试探")
                capture("exemax_2_maximized.png")
                subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"],
                               capture_output=True)
                print("exe 已退出")
                return
            capture("exemax_2_maximized.png")
    print("FAIL 真鼠标点最大化之后窗口仍然没铺满")
    subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"], capture_output=True)
    print("exe 已退出")


if __name__ == "__main__":
    main()
