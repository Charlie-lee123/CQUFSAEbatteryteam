# -*- coding: utf-8 -*-
r"""隔离探针：验证"让 Windows 自己播放最大化/还原/最小化动画"的可行性。

背景（2026-11-01）：
  无边框窗口被 Qt 设成 WS_POPUP（实测 GWL_STYLE=0x960F0000），Windows 对 POPUP
  窗口**不播放**最小化/还原的系统动画。开源方案（zhiyiYo/PyQt-Frameless-Window、
  wangwenx190/framelesshelper）的做法是：去掉 WS_POPUP、补回标准样式
  （WS_CAPTION|WS_SYSMENU|WS_MINIMIZEBOX|WS_MAXIMIZEBOX|WS_THICKFRAME），
  再用 WM_NCCALCSIZE 把非客户区高度让给客户区 —— 画面上没有系统标题栏，但
  shell 把它当普通窗口，**动画由 DWM 原生播放**。

本探针只验证三件事，不改产品文件：
  1. 补样式后**没有系统标题栏**（客户区尺寸 == 窗口尺寸）；
  2. 客户区内容能正常绘制（自绘标题栏仍在最上面）；
  3. 最大化 / 还原 / 最小化 能正常切换状态（动画肉眼另看）。

用法：QT_QPA_PLATFORM=windows python devtools/_probe_native_anim.py
"""

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_POPUP = 0x80000000
GWL_STYLE = -16
WM_NCCALCSIZE = 0x0083

WANTED = (WS_CAPTION | WS_SYSMENU | WS_THICKFRAME
          | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)


class Probe(QWidget):
    """无边框 + 标准样式 + WM_NCCALCSIZE 让位。"""

    def __init__(self):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("native-anim-probe")
        self.resize(760, 560)

    def showEvent(self, event):
        super().showEvent(event)
        hwnd = int(self.winId())
        if not hwnd:
            return
        u = ctypes.windll.user32
        style = u.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
        new_style = (style & ~WS_POPUP) | WANTED
        u.SetWindowLongW(hwnd, GWL_STYLE, new_style & 0xFFFFFFFF)
        # 让改动生效：SetWindowPos 带 SWP_FRAMECHANGED
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER = 0x0001, 0x0002, 0x0004
        SWP_FRAMECHANGED, SWP_NOACTIVATE = 0x0020, 0x0010
        u.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                       SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER
                       | SWP_FRAMECHANGED | SWP_NOACTIVATE)

    def nativeEvent(self, event_type, message):
        try:
            msg = wt.MSG.from_address(int(message))
        except Exception:
            return super().nativeEvent(event_type, message)
        if msg.message == WM_NCCALCSIZE and msg.wParam:
            # 返回 0：客户区 = 整个窗口矩形（系统标题栏/边框高度全让给客户区）
            return True, 0
        return super().nativeEvent(event_type, message)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#1b1d21"))
        p.setPen(QColor("#e6e6e6"))
        p.drawText(self.rect().adjusted(24, 24, -24, -24),
                   Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                   "原生动画探针\n\n"
                   "· 没有系统标题栏（整块都是客户区）\n"
                   "· 右上角应能双击最大化\n"
                   "· 最小化应看到系统动画")


def pump(app, ms):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def main() -> int:
    app = QApplication(sys.argv)
    w = Probe()
    w.show()
    pump(app, 400)
    u = ctypes.windll.user32
    hwnd = int(w.winId())
    style = u.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    win_w, win_h = r.right - r.left, r.bottom - r.top
    problems = []
    print(f"  GWL_STYLE = 0x{style:08X}")
    print(f"    POPUP={bool(style & WS_POPUP)} CAPTION={bool(style & WS_CAPTION)} "
          f"THICKFRAME={bool(style & WS_THICKFRAME)} "
          f"MINBOX={bool(style & WS_MINIMIZEBOX)} MAXBOX={bool(style & WS_MAXIMIZEBOX)}")
    print(f"  窗口(物理) = {win_w}x{win_h}   客户区(逻辑) = {w.width()}x{w.height()}")
    if style & WS_POPUP:
        problems.append("WS_POPUP 没去掉（Windows 不会播系统动画）")
    if not style & WS_CAPTION:
        problems.append("WS_CAPTION 没加上")
    # 客户区应等于窗口矩形（无系统标题栏）。物理 2x DPR → 逻辑 * 2。
    dpr = w.devicePixelRatioF() or 1.0
    if abs(win_w - w.width() * dpr) > 4 or abs(win_h - w.height() * dpr) > 4:
        problems.append(f"客户区与窗口矩形不一致（可能有系统标题栏）："
                        f"{win_w}x{win_h} vs {w.width() * dpr:.0f}x{w.height() * dpr:.0f}")

    w.showMaximized()
    pump(app, 400)
    print(f"  showMaximized → isMaximized={w.isMaximized()} geometry={w.geometry()}")
    if not w.isMaximized():
        problems.append("showMaximized 没生效")
    w.showNormal()
    pump(app, 400)
    print(f"  showNormal    → isMaximized={w.isMaximized()} geometry={w.geometry()}")
    if w.isMaximized():
        problems.append("showNormal 没生效")
    w.showMinimized()
    pump(app, 400)
    print(f"  showMinimized → isMinimized={w.isMinimized()}")
    if not w.isMinimized():
        problems.append("showMinimized 没生效")
    w.showNormal()
    pump(app, 400)
    print(f"  restore       → isMinimized={w.isMinimized()} geometry={w.geometry()}")

    print("\n== 结论 ==")
    print("  结构验证通过（动画本身请肉眼确认）" if not problems
          else "  ⛔ " + "；".join(problems))
    w.close()
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
