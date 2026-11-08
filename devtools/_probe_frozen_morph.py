# -*- coding: utf-8 -*-
"""验证：morph 期间 setUpdatesEnabled(False) 能否让补间跑满 165Hz。"""
import os, sys, time, statistics
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path: sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

_SNAP = {}
USE_FROZEN = "--frozen" in sys.argv

def main():
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAP.update({k: st.value(k) for k in st.allKeys()})
    app = QApplication.instance() or QApplication(sys.argv)
    import ui_anim
    from main_window import MainWindow
    ui_anim.set_frame_hz(ui_anim.display_max_hz())

    gaps = []
    last = {"t": None}
    orig_frame = MainWindow._on_win_geo_frame
    def w_frame(self, t):
        now = time.perf_counter()
        if last["t"] is not None: gaps.append((now - last["t"]) * 1000)
        last["t"] = now
        return orig_frame(self, t)
    MainWindow._on_win_geo_frame = w_frame

    if USE_FROZEN:
        orig_morph = MainWindow._win_geo_morph
        def w_morph(self, s, e, d=260):
            self.setUpdatesEnabled(False)
            self._morph_froze = True
            orig_morph(self, s, e, d)
        MainWindow._win_geo_morph = w_morph
        orig_done = MainWindow._on_win_geo_done
        def w_done(self):
            orig_done(self)
            if getattr(self, "_morph_froze", False):
                self._morph_froze = False
                self.setUpdatesEnabled(True)
        MainWindow._on_win_geo_done = w_done

    window = MainWindow()
    window._port_watch.stop()
    scr = app.primaryScreen().availableGeometry()
    window.setGeometry(scr.x() + 200, scr.y() + 100, 1134, 820)
    window.show()
    QTimer.singleShot(800, window.startup_fullscreen)
    QTimer.singleShot(1800, window._on_escape)
    def rep():
        g = [x for x in gaps if x < 500]
        if g:
            print(f"mode={'frozen' if USE_FROZEN else 'normal'} n={len(g)} "
                  f"med={statistics.median(g):.1f}ms p90={sorted(g)[int(len(g)*.9)]:.1f} "
                  f"max={max(g):.1f} -> 实际帧率 {1000/statistics.median(g):.0f}fps")
        app.quit()
    QTimer.singleShot(2700, rep)
    app.exec()
    st2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st2.allKeys()) - set(_SNAP): st2.remove(k)
    for k, v in _SNAP.items(): st2.setValue(k, v)
    st2.sync()
    return 0

if __name__ == "__main__":
    sys.exit(main())
