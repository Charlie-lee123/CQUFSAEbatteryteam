# -*- coding: utf-8 -*-
"""动画期间事件循环节流检测：独立 6ms 观察定时器 vs 补间 tick 间隔。"""
import os, sys, time, statistics
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path: sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtWidgets import QApplication

_SNAP = {}
obs_gaps = []

def main():
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAP.update({k: st.value(k) for k in st.allKeys()})
    app = QApplication.instance() or QApplication(sys.argv)
    import ui_anim
    from main_window import MainWindow
    ui_anim.set_frame_hz(ui_anim.display_max_hz())

    last = {"t": None}
    obs = QTimer()
    obs.setTimerType(Qt.TimerType.PreciseTimer)
    obs.setInterval(6)
    def ob():
        now = time.perf_counter()
        if last["t"] is not None:
            obs_gaps.append((now - last["t"]) * 1000)
        last["t"] = now
    obs.timeout.connect(ob)
    obs.start()

    tween_gaps = []
    lt = {"t": None}
    orig = MainWindow._on_win_geo_frame
    def wrap(self, t):
        orig(self, t)
        now = time.perf_counter()
        if lt["t"] is not None:
            tween_gaps.append((now - lt["t"]) * 1000)
        lt["t"] = now
    MainWindow._on_win_geo_frame = wrap

    window = MainWindow()
    window._port_watch.stop()
    scr = app.primaryScreen().availableGeometry()
    window.setGeometry(scr.x() + 200, scr.y() + 100, 1134, 820)
    window.show()

    def at(ms, name, fn):
        def fire():
            print(f"### {name}")
            fn()
        QTimer.singleShot(ms, fire)
    def _quit():
        obs.stop()
        app.quit()
    at(800, "max#1", window.startup_fullscreen)
    at(1800, "restore#1", window._on_escape)
    at(2600, "quit", _quit)
    app.exec()

    for name, gaps in (("observer", obs_gaps), ("tween", tween_gaps)):
        g = gaps[8:]
        if g:
            print(f"{name}: n={len(g)} median={statistics.median(g):.1f} "
                  f"mean={statistics.mean(g):.1f} p90={sorted(g)[int(len(g)*.9)]:.1f} "
                  f"max={max(g):.1f}")
    st2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st2.allKeys()) - set(_SNAP): st2.remove(k)
    for k, v in _SNAP.items(): st2.setValue(k, v)
    st2.sync()
    return 0

if __name__ == "__main__":
    sys.exit(main())
