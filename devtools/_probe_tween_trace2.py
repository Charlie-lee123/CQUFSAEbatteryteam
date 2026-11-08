# -*- coding: utf-8 -*-
import os, sys, time
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
    print("anim enabled:", ui_anim.animations_enabled())
    hz = ui_anim.set_frame_hz(ui_anim.display_max_hz())
    print(f"hz={hz} interval={ui_anim.frame_interval_ms()}")

    n = {"frame": 0}
    orig_frame = MainWindow._on_win_geo_frame
    def w_frame(self, t):
        n["frame"] += 1
        if n["frame"] <= 3:
            print(f"  frame #{n['frame']} t={t:.3f} fs={self.isFullScreen()}")
        return orig_frame(self, t)
    MainWindow._on_win_geo_frame = w_frame

    window = MainWindow()
    window._port_watch.stop()
    scr = app.primaryScreen().availableGeometry()
    window.setGeometry(scr.x() + 200, scr.y() + 100, 1134, 820)
    window.show()

    def do_max():
        window.startup_fullscreen()
        tk = ui_anim._ticker()
        print(f"after morph: ticker active={tk._timer.isActive()} "
              f"pending={len(tk._pending)} interval={tk._timer.interval()} "
              f"timerType={tk._timer.timerType()}")
    def report():
        print(f"frames total: {n['frame']}")
        app.quit()
    QTimer.singleShot(800, do_max)
    QTimer.singleShot(1600, report)
    app.exec()
    st2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st2.allKeys()) - set(_SNAP): st2.remove(k)
    for k, v in _SNAP.items(): st2.setValue(k, v)
    st2.sync()
    return 0

if __name__ == "__main__":
    sys.exit(main())
