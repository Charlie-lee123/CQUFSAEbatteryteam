# -*- coding: utf-8 -*-
"""定位补间两拍之间 ~25-30ms 的去向：paint / resize 子调用逐项计时。"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_SNAPSHOT: dict = {}
_T0 = time.perf_counter()
_ACC = {"paint": [0, 0.0], "resize": [0, 0.0], "remember": [0, 0.0],
        "padding": [0, 0.0], "compact": [0, 0.0], "snapzoom": [0, 0.0]}
_LAST = {"tick": None}


def main() -> int:
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    app = QApplication.instance() or QApplication(sys.argv)
    import ui_anim
    from main_window import MainWindow

    ui_anim.set_frame_hz(ui_anim.display_max_hz())

    def wrap(cls, name, label):
        orig = getattr(cls, name)

        def wrapped(self, *a, **kw):
            t0 = time.perf_counter()
            try:
                return orig(self, *a, **kw)
            finally:
                dt = time.perf_counter() - t0
                _ACC[label][0] += 1
                _ACC[label][1] += dt
                if dt > 0.012:
                    print(f"{(time.perf_counter() - _T0) * 1000:8.1f}ms  "
                          f"{label} {dt * 1000:.1f}ms")
        setattr(cls, name, wrapped)

    wrap(MainWindow, "paintEvent", "paint")
    wrap(MainWindow, "resizeEvent", "resize")
    wrap(MainWindow, "_remember_normal_geometry", "remember")
    wrap(MainWindow, "_sync_fullscreen_padding", "padding")
    wrap(MainWindow, "_sync_compact", "compact")
    wrap(MainWindow, "_sync_snap_zoom", "snapzoom")

    # 补间帧间隔
    orig_frame = MainWindow._on_win_geo_frame

    def wrap_frame(self, t):
        t0 = time.perf_counter()
        orig_frame(self, t)
        now = time.perf_counter()
        gap = ((now - _LAST["tick"]) * 1000.0) if _LAST["tick"] else -1
        _LAST["tick"] = now
        if gap > 20:
            print(f"{now * 1000 - _T0 * 1000:8.1f}ms  frameGap {gap:.1f}ms "
                  f"(t={t:.3f})")

    MainWindow._on_win_geo_frame = wrap_frame

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

    at(800, "max#1", window.startup_fullscreen)
    at(1800, "restore#1", window._on_escape)
    at(2800, "max#2", window.startup_fullscreen)
    at(3800, "restore#2", window._on_escape)
    def _quit():
        print("### quit")
        app.quit()

    at(4600, "quit", _quit)

    app.exec()
    print("\n== 计时汇总 ==")
    for k, (n, total) in _ACC.items():
        if n:
            print(f"  {k:<10} n={n:<5} total={total * 1000:7.1f}ms "
                  f"avg={total / n * 1000:5.1f}ms")
    st2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st2.allKeys()) - set(_SNAPSHOT):
        st2.remove(k)
    for k, v in _SNAPSHOT.items():
        st2.setValue(k, v)
    st2.sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
