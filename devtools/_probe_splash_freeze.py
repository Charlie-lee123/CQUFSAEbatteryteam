# -*- coding: utf-8 -*-
"""跑真实启动序列（main.main 的开屏+定时构造链），用 5ms 看门狗抓主线程
冻结 >40ms 的时刻，并对照开屏播放头（视频时间）定位卡点。"""
import os, sys, time
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path: sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

import splash_anim
import ui_anim

# 看门狗：5ms 精密定时器，间隔 >40ms 记一次冻结
gaps = []
last = {"t": None}
wd = None

def install_watchdog(app):
    global wd
    from PySide6.QtCore import Qt, QTimer
    wd = QTimer()
    wd.setTimerType(Qt.TimerType.PreciseTimer)
    wd.setInterval(5)
    def tick():
        now = time.perf_counter()
        if last["t"] is not None:
            g = (now - last["t"]) * 1000
            if g > 40:
                gaps.append((now, g))
        last["t"] = now
    wd.timeout.connect(tick)
    wd.start()

# 包住关键节点打时间戳
marks = {}
_orig_play = splash_anim.play_configured
def w_play(*a, **k):
    t0 = time.perf_counter()
    r = _orig_play(*a, **k)
    marks["splash_start"] = t0
    if r is not None:
        orig_show_frame = r._show_frame
        def w_sf(idx):
            marks.setdefault("first_frame", time.perf_counter())
            return orig_show_frame(idx)
        r._show_frame = w_sf
        orig_pause = r.pause_clock
        def w_pause():
            marks["pause_clock"] = time.perf_counter()
            return orig_pause()
        r.pause_clock = w_pause
    return r
splash_anim.play_configured = w_play

import main_window
_orig_init = main_window.MainWindow.__init__
def w_init(self, *a, **k):
    t0 = time.perf_counter()
    _orig_init(self, *a, **k)
    marks["mw_init_end"] = time.perf_counter()
    print(f"MainWindow.__init__ took {(time.perf_counter()-t0)*1000:.0f}ms")
main_window.MainWindow.__init__ = w_init

import main
main.install_watchdog = install_watchdog  # main.py 不认识，仅占位

# 直接跑 main()（它会 app.exec() 到退出）
# ⛔ main() 会 QSettings 快照还原吗？不会 —— 它就是真实启动。跑 9s 后强退。
from PySide6.QtCore import QTimer
_orig_exec = None

import PySide6.QtWidgets as QtWidgets
_orig_exec_app = QtWidgets.QApplication.exec

def w_exec(*a, **k):
    install_watchdog(QtWidgets.QApplication.instance())
    def stop():
        wd.stop()
        t_splash = marks.get("splash_start")
        print("== 主线程冻结（>40ms）==")
        for t, g in gaps:
            rel = f"splash+{(t - t_splash)*1000:.0f}ms" if t_splash else "?"
            print(f"  {rel:<16} {g:.0f}ms")
        for k_, v in marks.items():
            if t_splash:
                print(f"  mark {k_}: splash+{(v - t_splash)*1000:.0f}ms")
        QtWidgets.QApplication.quit()
    QTimer.singleShot(9000, stop)
    return _orig_exec_app()
QtWidgets.QApplication.exec = w_exec

sys.argv = ["SerialAssistant"]
main.main()
