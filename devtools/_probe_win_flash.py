# -*- coding: utf-8 -*-
r"""逐帧诊断：最大化/还原/最小化/关闭时"闪"在哪一帧。

用户 2026-10-31：「最大化最小化还原关闭这类操作仍然不连贯会闪，请逐帧找原因」。

做法：调动作后每 ~4ms 采一次【窗口不透明度 + 整个屏幕的平均亮度 + 覆盖层几何】，
拼成逐帧序列。判据：
  · 亮度出现"先暗后亮/先亮后暗"的折返 = 闪（非单调）
  · 不透明度在开头就骤降（0.3x）= 窗口被压暗，露出桌面/背景 = 暗闪
  · 覆盖层存在但几何不连续跳变 = 跳帧
只读诊断，不写产品代码；QSettings 快照还原。
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def pump(app, ms=200):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.004)


def luma(img) -> float:
    if hasattr(img, "toImage"):
        img = img.toImage()
    img = img.convertToFormat(QImage.Format.Format_RGB32)
    w, h = img.width(), img.height()
    if not w or not h:
        return 0.0
    tx, ty = max(1, w // 120), max(1, h // 90)
    total, n = 0.0, 0
    for y in range(0, h, ty):
        for x in range(0, w, tx):
            c = img.pixelColor(x, y)
            total += 0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()
            n += 1
    return total / max(1, n)


def sample(app, screen, window, steps=70):
    rows = []
    for _ in range(steps):
        app.processEvents()
        op = round(window.windowOpacity(), 3) if window is not None else 1.0
        lab = getattr(window, "_morph_label", None) if window is not None else None
        geo = lab.geometry() if lab is not None else None
        rows.append((op, luma(screen.grabWindow(0)), geo))
        time.sleep(0.004)
    return rows


def analyse(name, rows, shrinking=False):
    print(f"\n== {name} ==")
    ops = [r[0] for r in rows]
    lum = [r[1] for r in rows]
    print(f"  不透明度: {[round(x, 2) for x in ops[:14]]}")
    print(f"  屏幕亮度: {[round(x, 1) for x in lum[:14]]}")
    # 亮度折返：先降后升或先升后降（幅度 > 1.2）
    tip = []
    for i in range(1, len(lum) - 1):
        d1, d2 = lum[i] - lum[i - 1], lum[i + 1] - lum[i]
        if d1 < -0.8 and d2 > 0.8:
            tip.append(f"帧{i} 亮度先降{d1:.1f}再升{d2:.1f}（暗闪）")
        if d1 > 0.8 and d2 < -0.8:
            tip.append(f"帧{i} 亮度先升{d1:.1f}再降{d2:.1f}（亮闪）")
    # 不透明度骤降
    dips = [i for i, o in enumerate(ops[:8]) if o < 0.9]
    if dips:
        tip.append(f"前 8 帧不透明度已降到 {min(ops[:8]):.2f}（窗口被压暗）")
    geos = [r[2] for r in rows if r[2] is not None]
    if len(geos) >= 2 and not shrinking:
        widths = [g.width() for g in geos]
        back = [i for i in range(1, len(widths)) if widths[i] + 2 < widths[i - 1]]
        if back:
            tip.append(f"覆盖层宽度回退（跳帧）：{widths}")
        print(f"  覆盖层宽度: {widths[:10]}")
    print(("  ⛔ " + "；".join(tip)) if tip else "  ✓ 无亮度折返/压暗")


def main() -> int:
    app = QApplication(sys.argv)
    import ui_theme
    app.setStyle("Fusion")
    app.setFont(ui_theme.ui_font(12.5))
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    screen = app.primaryScreen()
    window = None
    try:
        from main_window import MainWindow
        window = MainWindow()
        window.resize(800, 600)
        window.show()
        pump(app, 300)

        window.startup_fullscreen()
        analyse("最大化（普通→全屏）", sample(app, screen, window))
        pump(app, 400)

        window._on_escape()
        analyse("还原（全屏→普通）", sample(app, screen, window))
        pump(app, 400)

        window._minimize_animated()
        analyse("最小化", sample(app, screen, window, 45), shrinking=True)
        pump(app, 400)
        window.showNormal()
        window.setWindowOpacity(1.0)
        pump(app, 300)
    finally:
        if window is not None:
            try:
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
