# -*- coding: utf-8 -*-
r"""对**真实 exe** 做"最大化前后 + 动画逐帧"的抓屏与测量（双进程思路）。

为什么必须这样：
- 同进程里 `QWidget.grab()` 在窗口缩放/全屏切换期间返回**全黑**；
- 同进程里 `QScreen.grabWindow()` 也拿不到自己的内容；
- 但**另一个进程**抓整屏是好的（`_probe_exe_dim.py` 已验证）。

这个脚本一次干两件事：
1. 启动 exe → 抓基线 → 用真实鼠标点"接收区最大化" → 连续抓帧（看跳变）→ 抓终态；
2. 逐块量文字/图标的亮度（回答"展开后字体暗淡"）。

用法：python devtools\_probe_real_anim.py
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

from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication                # noqa: E402

u = ctypes.windll.user32
u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u.SetForegroundWindow.argtypes = [wintypes.HWND]
u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                          wintypes.DWORD, ctypes.c_void_p]

EXE = os.path.join(_ROOT, "发布", "SerialAssistant.exe")
OUT = os.path.join(_ROOT, "_check")


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
    pids = pids_of()
    for _ in range(40):
        found = []

        def cb(h, _l):
            if u.IsWindowVisible(h) and u.GetWindow(h, 4) == 0:
                rect = wintypes.RECT()
                u.GetWindowRect(h, ctypes.byref(rect))
                if rect.right - rect.left > 600:
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


def measure(img, box, tag):
    x0, y0, x1, y1 = box
    x1 = min(x1, img.width())
    y1 = min(y1, img.height())
    mx = 0
    lit = 0
    total = 0
    n = 0
    for y in range(max(0, y0), y1, 2):
        for x in range(max(0, x0), x1, 2):
            c = img.pixelColor(x, y)
            v = (c.red() + c.green() + c.blue()) // 3
            mx = max(mx, v)
            total += v
            n += 1
            if v > 150:
                lit += 1
    print(f"    {tag}: 最亮={mx} 均亮={(total / n if n else 0):.1f} 亮于150={lit}")


def main() -> int:
    app = QApplication(sys.argv)

    subprocess.run(["taskkill", "/IM", "SerialAssistant.exe", "/F"],
                   capture_output=True)
    time.sleep(1.5)
    os.startfile(EXE)
    time.sleep(8)
    hwnd = find_main_hwnd()
    if not hwnd:
        print("找不到主窗口")
        return 1
    rect = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    print(f"窗口 {rect.left},{rect.top} {w}x{h}")

    u.SetForegroundWindow(hwnd)
    time.sleep(1.2)

    print("== 最大化前")
    base = app.primaryScreen().grabWindow(0).toImage()
    measure(base, (60, 120, 700, 170), "占位提示那一行")
    measure(base, (int(w * 0.93), int(h * 0.30), w - 20, int(h * 0.72)),
            "右侧功能列")

    # 点"接收区最大化"：右侧功能列最下面那颗
    cx = rect.left + int(w * 0.955)
    cy = rect.top + int(h * 0.655)
    u.SetCursorPos(cx, cy)
    time.sleep(0.3)
    u.mouse_event(0x0002, 0, 0, 0, None)
    time.sleep(0.05)
    u.mouse_event(0x0004, 0, 0, 0, None)

    print("== 动画逐帧（每 40ms 一张，看跳变）")
    shots = []
    t0 = time.perf_counter()
    next_at = 0.0
    while len(shots) < 10 and time.perf_counter() - t0 < 0.9:
        if (time.perf_counter() - t0) * 1000 >= next_at:
            shots.append(((time.perf_counter() - t0) * 1000,
                          app.primaryScreen().grabWindow(0).toImage()))
            next_at += 40
        time.sleep(0.004)
    time.sleep(0.6)

    print("== 最大化后")
    after = app.primaryScreen().grabWindow(0).toImage()
    measure(after, (60, 120, 700, 170), "占位提示那一行")
    measure(after, (int(w * 0.93), int(h * 0.30), w - 20, int(h * 0.72)),
            "右侧功能列")

    # 拼帧条（裁左半屏，窗口在左上）
    strip = min(10, len(shots))
    cw = min(1100, after.width())
    ch = int(after.height() * 0.62)
    sheet = QImage(16 + (cw + 16) * strip, ch + 40, QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#0D0D0F"))
    painter = QPainter(sheet)
    painter.setFont(QFont("Microsoft YaHei UI", 10))
    for i, (ms, img) in enumerate(shots[:strip]):
        x = 16 + i * (cw + 16)
        painter.drawImage(x, 34, img.copy(0, 0, cw, ch))
        painter.setPen(QColor("#F2F3F5"))
        painter.drawText(x + 6, 24, f"{ms:.0f}ms")
    painter.end()
    sheet.save(os.path.join(OUT, "real_anim_strip.png"), "PNG")
    before_path = os.path.join(OUT, "real_anim_before.png")
    base.copy(0, 0, cw, ch).save(before_path, "PNG")
    after.copy(0, 0, cw, ch).save(os.path.join(OUT, "real_anim_after.png"), "PNG")
    print("帧条:", os.path.join(OUT, "real_anim_strip.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
