# -*- coding: utf-8 -*-
"""产物级验证：exe 的**起手姿态**是否跟着「定制功能 → 启动时最大化」走。

- 开关开（默认）→ 启动即整窗全屏：窗口物理矩形 == 所在显示器矩形；
- 开关关 → 启动是普通窗口：矩形明显小于显示器（还能看到自绘标题栏那一排）。

跑完把 QSettings 原样还回去（MimoWorks/SerialAssistant）。
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _exe_check as ec                              # 复用窗口枚举 / DPI 声明 / 截图

from PySide6.QtCore import QSettings

user32 = ec.user32
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.MonitorFromWindow.restype = ctypes.c_void_p


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def rect_of(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def monitor_rect(hwnd):
    hmon = user32.MonitorFromWindow(wt.HWND(hwnd), 2)      # MONITOR_DEFAULTTONEAREST
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi))
    m = mi.rcMonitor
    return (m.left, m.top, m.right, m.bottom)


def kill_exe():
    subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"],
                   capture_output=True)
    time.sleep(1.5)


def launch(timeout=90):
    """启动 exe 并等窗口出来（单文件冷启动要解包）。返回 hwnd。"""
    subprocess.Popen([ec.EXE])
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = ec.our_windows()
        if found:
            return found[0][0]
        time.sleep(1)
    return None


def measure(label, settle=5.0):
    """启动一次，量窗口矩形与显示器矩形。"""
    kill_exe()
    hwnd = launch()
    if not hwnd:
        print(f"FAIL {label}：没等到窗口")
        return None
    time.sleep(settle)                     # 等入场动画 / 全屏延迟核对（180+600ms）
    win = rect_of(hwnd)
    mon = monitor_rect(hwnd)
    gap = (win[0] - mon[0], win[1] - mon[1], mon[2] - win[2], mon[3] - win[3])
    print(f"{label}: 窗口 {win} / 显示器 {mon} / 四边露出 {gap}")
    return win, mon, gap


def main() -> int:
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    failures = []
    try:
        # ---- 1) 开关**关**：起手应当是普通窗口 ----
        settings.setValue("custom/startup_maximized", False)
        settings.sync()
        result = measure("关掉开关")
        if result:
            win, mon, gap = result
            wide = (mon[2] - mon[0]) - (win[2] - win[0])
            high = (mon[3] - mon[1]) - (win[3] - win[1])
            if wide < 200 and high < 200:
                failures.append(f"关掉开关后仍是整屏（宽差 {wide}，高差 {high}）")
            else:
                print(f"  普通窗口判定 OK（比屏幕窄 {wide}px、矮 {high}px）")
        kill_exe()

        # ---- 2) 开关**开**：起手应当是整窗全屏（盖住任务栏）----
        settings.setValue("custom/startup_maximized", True)
        settings.sync()
        result = measure("开着开关")
        if result:
            _win, _mon, gap = result
            if any(g != 0 for g in gap):
                failures.append(f"开着开关却没有铺满显示器（四边露出 {gap}）")
            else:
                print("  整窗全屏判定 OK（四边 gap 全 0）")
        kill_exe()
    finally:
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()
        print("QSettings 已还原")

    if failures:
        for line in failures:
            print("FAIL", line)
        return 1
    print("PASS 起手姿态跟着「启动时最大化」走")
    return 0


if __name__ == "__main__":
    sys.exit(main())
