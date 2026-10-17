# -*- coding: utf-8 -*-
r"""驱动**真实 exe** 复现"接收区最大化后变暗"：重启程序 → 截基线 → 真鼠标点展开 → 再截。

只依赖 ctypes + Qt，不引第三方库（本机开发 venv 里没有 psutil）。
判据是**整窗均亮 + 亮像素数**：变暗的话两项都会明显下降。
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtWidgets import QApplication          # noqa: E402

u = ctypes.windll.user32
u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u.SetForegroundWindow.argtypes = [wintypes.HWND]
u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                          wintypes.DWORD, ctypes.c_void_p]

EXE = os.path.join(_ROOT, "发布", "SerialAssistant.exe")


def pids_of(name: str = "SerialAssistant.exe") -> set[int]:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV"],
                         capture_output=True, text=True, errors="ignore").stdout
    pids = set()
    for line in out.splitlines()[1:]:
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def find_main_hwnd():
    """按进程找主窗口（标题栏是自绘的，标题未必叫那个）。"""
    pids = pids_of()
    for _ in range(30):
        found = []

        def cb(h, _lparam):
            if u.IsWindowVisible(h) and u.GetWindow(h, 4) == 0:      # 无 owner
                rect = wintypes.RECT()
                u.GetWindowRect(h, ctypes.byref(rect))
                if rect.right - rect.left > 400:
                    found.append(h)
            return True

        u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND,
                                         wintypes.LPARAM)(cb), 0)
        for h in found:
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value in pids:
                return h
        time.sleep(0.5)
    return None


def grab(x, y, w, h, path):
    app = QApplication.instance() or QApplication(sys.argv)
    img = app.primaryScreen().grabWindow(0, x, y, w, h).toImage()
    img.save(path)
    return img


def stats(img, tag):
    n = bright = 0
    total = 0
    for yy in range(0, img.height(), 2):
        for xx in range(0, img.width(), 2):
            c = img.pixelColor(xx, yy)
            v = (c.red() + c.green() + c.blue()) // 3
            total += v
            n += 1
            if v > 100:
                bright += 1
    print(f"  {tag}: 均亮={total / n:.1f}  亮像素={bright}  ({img.width()}x{img.height()})")


def main() -> int:
    QApplication(sys.argv)          # 只为建 QScreen 用（grab 靠它）
    subprocess.run(["taskkill", "/IM", "SerialAssistant.exe", "/F"],
                   capture_output=True)
    time.sleep(1.5)
    os.startfile(EXE)
    time.sleep(8)
    hwnd = find_main_hwnd()
    print("主窗口 hwnd =", hwnd)
    if not hwnd:
        return 1
    rect = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(rect))
    x, y, w, h = rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    print(f"窗口 {x},{y} {w}x{h}")
    u.SetForegroundWindow(hwnd)
    time.sleep(1.0)
    stats(grab(x, y, w, h, os.path.join(_ROOT, "_check", "exe_before.png")), "常态")

    # 右侧功能列最下面那颗 = 接收区最大化（约在窗口宽 96%、高 65% 处）
    cx, cy = x + int(w * 0.960), y + int(h * 0.652)
    print("点击展开按钮 @", cx, cy)
    u.SetCursorPos(cx, cy)
    time.sleep(0.4)
    u.mouse_event(0x0002, 0, 0, 0, None)
    time.sleep(0.06)
    u.mouse_event(0x0004, 0, 0, 0, None)
    time.sleep(2.2)

    u.GetWindowRect(hwnd, ctypes.byref(rect))
    x2, y2, w2, h2 = rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    print(f"展开后窗口 {x2},{y2} {w2}x{h2}")
    stats(grab(x2, y2, w2, h2, os.path.join(_ROOT, "_check", "exe_after.png")),
          "展开后")
    return 0


if __name__ == "__main__":
    sys.exit(main())
