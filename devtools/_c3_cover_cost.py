# -*- coding: utf-8 -*-
"""覆盖层单帧成本分解（win-cover-3）：定位 _on_cover_tick 每帧 28ms 的元凶。

被测的三种做法（都在 QPixmap / QLabel 层，不碰产品源码）：
  A 现状：_cover_pix.scaled(phys, FastTransformation) + label.setPixmap
  B 变体：_cover_pix.scaled(phys, FastTransformation) 只算不设
  C 变体：label.setPixmap(同一尺寸，缓存命中)
  D 替代：固定满屏 pixmap，paintEvent 里 drawPixmap(destRect, pix, srcRect)

跑法：.venv/Scripts/python.exe devtools/_c3_cover_cost.py
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRect, Qt                     # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap     # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402


def stat(name: str, xs: list[float]) -> None:
    xs = sorted(xs)
    print(f"{name:44s} p50={statistics.median(xs):6.2f}ms "
          f"p95={xs[int(len(xs) * 0.95)]:6.2f}ms max={xs[-1]:6.2f}ms")


def main() -> int:
    app = QApplication(sys.argv[:1])
    src = QPixmap(3200, 2000)
    src.fill(QColor(24, 24, 28))
    # 画点纹理，避免整片纯色让缩放被优化
    p = QPainter(src)
    for i in range(0, 3200, 40):
        p.fillRect(i, 0, 6, 2000, QColor(90, 120, 200) if i % 80 else QColor(230, 230, 230))
    p.end()
    src.setDevicePixelRatio(2.0)

    label = QLabel(None)
    label.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
    label.setAttribute(Qt.WA_ShowWithoutActivating, True)
    label.setScaledContents(False)
    label.setGeometry(0, 0, 1134, 820)
    label.show()
    app.processEvents()

    phys_list = [(int(2268 + (3200 - 2268) * k / 17), int(1640 + (2000 - 1640) * k / 17))
                 for k in range(18)]

    # A 现状
    ta = []
    for pw, ph in phys_list:
        t = time.perf_counter()
        sc = src.scaled(pw, ph, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        sc.setDevicePixelRatio(2.0)
        label.setPixmap(sc)
        app.processEvents()
        ta.append((time.perf_counter() - t) * 1000)
    stat("A scaled(Fast)+setPixmap（现状）", ta)

    # B 只 scaled
    tb = []
    for pw, ph in phys_list:
        t = time.perf_counter()
        sc = src.scaled(pw, ph, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        tb.append((time.perf_counter() - t) * 1000)
    stat("B scaled(Fast) 单独", tb)

    # B2 scaled smooth
    tb2 = []
    for pw, ph in phys_list:
        t = time.perf_counter()
        _ = src.scaled(pw, ph, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        tb2.append((time.perf_counter() - t) * 1000)
    stat("B2 scaled(Smooth) 单独", tb2)

    # C 同尺寸 setPixmap（缓存命中）
    fixed = src.scaled(3050, 1942, Qt.IgnoreAspectRatio, Qt.FastTransformation)
    fixed.setDevicePixelRatio(2.0)
    tc = []
    for _ in range(8):
        t = time.perf_counter()
        label.setPixmap(fixed)
        app.processEvents()
        tc.append((time.perf_counter() - t) * 1000)
    stat("C setPixmap 同尺寸（缓存命中）", tc)

    # D 固定满屏 + paintEvent drawPixmap
    class Painter(QWidget):
        def __init__(self):
            super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint
                             | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
            self.pix = src
            self.rect_now = QRect(0, 0, 1134, 820)
            self.setGeometry(self.rect_now)

        def paintEvent(self, _ev):
            pa = QPainter(self)
            pa.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            pa.drawPixmap(self.rect_now, self.pix,
                          QRect(0, 0, self.pix.width(), self.pix.height()))

    w2 = Painter()
    w2.show()
    app.processEvents()
    td = []
    for pw, ph in phys_list:
        t = time.perf_counter()
        w2.rect_now = QRect(0, 0, pw // 2, ph // 2)
        w2.setGeometry(0, 0, pw // 2, ph // 2)
        w2.repaint()
        app.processEvents()
        td.append((time.perf_counter() - t) * 1000)
    stat("D 固定pixmap+drawPixmap 每帧", td)

    label.hide()
    w2.hide()
    extra(app)
    return 0



# ---- 追加：dpr=1（1600x1000 表面）与"只 setGeometry" 的对照 ----
def extra(app):
    import statistics as st
    src1 = QPixmap(1600, 1000)
    src1.fill(QColor(24, 24, 28))
    p = QPainter(src1)
    for i in range(0, 1600, 20):
        p.fillRect(i, 0, 3, 1000, QColor(90, 120, 200) if i % 40 else QColor(230, 230, 230))
    p.end()
    lab = QLabel(None)
    lab.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint
                       | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
    lab.setAttribute(Qt.WA_ShowWithoutActivating, True)
    lab.setScaledContents(False)
    lab.setGeometry(0, 0, 1134, 820)
    lab.show()
    app.processEvents()
    phys = [(int(1134 + (1600 - 1134) * k / 17), int(820 + (1000 - 820) * k / 17))
            for k in range(18)]
    ta = []
    for pw, ph in phys:
        t = time.perf_counter()
        sc = src1.scaled(pw, ph, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        lab.setPixmap(sc)
        lab.setGeometry(12, 8, pw, ph)
        app.processEvents()
        ta.append((time.perf_counter() - t) * 1000)
    stat("E dpr=1 scaled+setPixmap+setGeometry", ta)
    # 只 setGeometry（pixmap 固定）
    fixed = src1.scaled(1600, 1000, Qt.IgnoreAspectRatio, Qt.FastTransformation)
    lab.setPixmap(fixed)
    app.processEvents()
    tg = []
    for pw, ph in phys:
        t = time.perf_counter()
        lab.setGeometry(12, 8, pw, ph)
        app.processEvents()
        tg.append((time.perf_counter() - t) * 1000)
    stat("F dpr=1 固定pixmap + 只 setGeometry", tg)
    lab.hide()


if __name__ == "__main__":
    sys.exit(main())
