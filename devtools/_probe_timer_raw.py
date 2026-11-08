# -*- coding: utf-8 -*-
"""裸 QTimer(6ms, PreciseTimer) 对照实验：
① 空转；② 每 tick resize 主窗；③ 每 tick resize 主窗但不显示。
输出实际投递间隔分布。"""
import os
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

app = QApplication(sys.argv)
win = QLabel("x")
win.resize(800, 600)

gaps: list[float] = []
last = {"t": None}
mode = {"n": 0}


def make_timer(resize: bool, shown: bool):
    if shown:
        win.show()
    t = QTimer()
    t.setTimerType(Qt.TimerType.PreciseTimer)
    t.setInterval(6)

    def tick():
        now = time.perf_counter()
        if last["t"] is not None:
            gaps.append((now - last["t"]) * 1000)
        last["t"] = now
        mode["n"] += 1
        if resize:
            w = 800 + (mode["n"] % 60)
            win.resize(w, 600)
        if mode["n"] >= 300:
            t.stop()
    t.timeout.connect(tick)
    return t


which = sys.argv[1] if len(sys.argv) > 1 else "idle"
t = make_timer(resize=(which == "resize"), shown=(which != "hidden"))
t.start()
QTimer.singleShot(3000, app.quit)
app.exec()

if gaps:
    gaps = gaps[10:]
    print(f"mode={which} n={len(gaps)} "
          f"median={statistics.median(gaps):.1f}ms "
          f"mean={statistics.mean(gaps):.1f}ms "
          f"p90={sorted(gaps)[int(len(gaps) * 0.9)]:.1f}ms "
          f"max={max(gaps):.1f}ms")
