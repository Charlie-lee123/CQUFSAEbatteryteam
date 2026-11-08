# -*- coding: utf-8 -*-
"""量化 QScreen.grabWindow 按窗口矩形抓取的耗时（vs 整屏）。"""
import os, sys, time, statistics
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
from PySide6.QtWidgets import QApplication, QLabel
app = QApplication(sys.argv)
win = QLabel("x")
win.setGeometry(200, 100, 1134, 820)
win.show()
scr = app.primaryScreen()

def bench(label, fn, n=20):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    print(f"{label}: med={statistics.median(ts):.1f}ms min={min(ts):.1f} max={max(ts):.1f}")

g = win.geometry()
bench("整屏 grabWindow(0)", lambda: scr.grabWindow(0))
bench("窗口矩形 grabWindow(0,x,y,w,h)", lambda: scr.grabWindow(0, g.x(), g.y(), g.width(), g.height()))
bench("整屏 grabWindow(0) 再一次", lambda: scr.grabWindow(0))
bench("窗口矩形 再一次", lambda: scr.grabWindow(0, g.x(), g.y(), g.width(), g.height()))
