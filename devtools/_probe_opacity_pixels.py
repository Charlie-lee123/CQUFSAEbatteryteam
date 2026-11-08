# -*- coding: utf-8 -*-
r"""诊断：setWindowOpacity 在这扇无边框自绘窗上**是否真的改变屏幕像素**。

之前 _probe_window_fx 只读了 windowOpacity() 属性值（那只是 Qt 侧的数字），
没验证 DWM 合成后屏幕上到底变没变。这里抓**整个屏幕**的平均亮度对比：
  opacity=1.0 vs opacity=0.0 —— 若两者亮度几乎一样，说明 opacity 是空操作，
  所有"淡入淡出"在真机上根本看不见（用户"还是没有动画"的根因）。
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def pump(app, ms=250):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def mean_luma(img) -> float:
    if hasattr(img, "toImage"):        # grabWindow 返回 QPixmap
        img = img.toImage()
    img = img.convertToFormat(QImage.Format.Format_RGB32)
    w, h = img.width(), img.height()
    if w == 0 or h == 0:
        return 0.0
    step_x = max(1, w // 160)
    step_y = max(1, h // 120)
    total, n = 0.0, 0
    for y in range(0, h, step_y):
        for x in range(0, w, step_x):
            c = img.pixelColor(x, y)
            total += 0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()
            n += 1
    return total / max(1, n)


def main() -> int:
    app = QApplication(sys.argv)
    import ui_theme
    app.setStyle("Fusion")
    app.setFont(ui_theme.ui_font(12.5))

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    window = None
    try:
        from main_window import MainWindow
        window = MainWindow()
        window.resize(900, 700)
        window.show()
        pump(app, 400)

        screen = app.primaryScreen()
        window.setWindowOpacity(1.0)
        pump(app, 300)
        img_full = screen.grabWindow(0)
        luma_full = mean_luma(img_full)

        window.setWindowOpacity(0.0)
        pump(app, 300)
        img_zero = screen.grabWindow(0)
        luma_zero = mean_luma(img_zero)

        window.setWindowOpacity(0.5)
        pump(app, 300)
        img_half = screen.grabWindow(0)
        luma_half = mean_luma(img_half)

        print(f"  屏幕平均亮度  opacity=1.0 → {luma_full:.2f}")
        print(f"               opacity=0.5 → {luma_half:.2f}")
        print(f"               opacity=0.0 → {luma_zero:.2f}")
        d_zero = abs(luma_full - luma_zero)
        d_half = abs(luma_full - luma_half)
        print(f"  |1.0-0.0| = {d_zero:.2f}   |1.0-0.5| = {d_half:.2f}")
        if d_zero < 1.0:
            print("  ⛔ opacity 是**空操作**：DWM 没按它合成，淡入淡出在真机看不见")
        else:
            print("  ✓ opacity 真实生效（像素确实变了）→ 淡入淡出可见，"
                  "只是幅度/时长不够；要更强需要几何/缩放覆盖层")
    finally:
        if window is not None:
            try:
                window.setWindowOpacity(1.0)
                window._closing = True
                window.close()
            except Exception:
                pass
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            if value is None:
                settings.remove(key)
            else:
                settings.setValue(key, value)
        settings.sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
