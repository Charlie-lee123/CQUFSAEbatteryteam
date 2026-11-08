# -*- coding: utf-8 -*-
"""直接检查 ticker 的 interval 与真实触发间隔。"""
import os, sys, time, statistics
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path: sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

_SNAP = {}

def main():
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAP.update({k: st.value(k) for k in st.allKeys()})
    app = QApplication.instance() or QApplication(sys.argv)
    import ui_anim
    from main_window import MainWindow
    hz = ui_anim.set_frame_hz(ui_anim.display_max_hz())
    print(f"set hz={hz} interval={ui_anim.frame_interval_ms()}")

    tk = ui_anim._ticker()
    print(f"pre-warm ticker interval={tk._timer.interval()} active={tk._timer.isActive()}")

    tick_gaps, tick_costs = [], []
    last = {"t": None}
    orig_tick = ui_anim._Ticker._tick
    def wrap_tick(self):
        now = time.perf_counter()
        if last["t"] is not None:
            tick_gaps.append((now - last["t"]) * 1000)
        last["t"] = now
        t0 = time.perf_counter()
        orig_tick(self)
        tick_costs.append((time.perf_counter() - t0) * 1000)
    ui_anim._Ticker._tick = wrap_tick

    window = MainWindow()
    window._port_watch.stop()
    scr = app.primaryScreen().availableGeometry()
    window.setGeometry(scr.x() + 200, scr.y() + 100, 1134, 820)
    window.show()
    print(f"post-build ticker interval={ui_anim._ticker()._timer.interval()}")

    def at(ms, name, fn):
        def fire():
            print(f"### {name} interval={ui_anim._ticker()._timer.interval()}")
            fn()
        QTimer.singleShot(ms, fire)
    def _quit():
        app.quit()
    at(800, "max#1", window.startup_fullscreen)
    at(1800, "restore#1", window._on_escape)
    at(2600, "quit", _quit)
    app.exec()

    for name, g in (("tickGap", tick_gaps), ("tickCost", tick_costs)):
        g = g[20:]
        if g:
            print(f"{name}: n={len(g)} median={statistics.median(g):.1f} "
                  f"p90={sorted(g)[int(len(g)*.9)]:.1f} max={max(g):.1f}")
    st2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st2.allKeys()) - set(_SNAP): st2.remove(k)
    for k, v in _SNAP.items(): st2.setValue(k, v)
    st2.sync()
    return 0

if __name__ == "__main__":
    sys.exit(main())
