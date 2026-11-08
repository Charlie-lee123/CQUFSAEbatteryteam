# -*- coding: utf-8 -*-
"""三层同时打点：ticker tick / tween.advance / geo frame 回调。"""
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
    print(f"hz={hz}")

    gaps = {"tick": [], "adv": [], "frame": []}
    last = {"tick": None, "adv": None, "frame": None}

    orig_tick = ui_anim._Ticker._tick
    def w_tick(self):
        now = time.perf_counter()
        if last["tick"] is not None: gaps["tick"].append((now - last["tick"]) * 1000)
        last["tick"] = now
        orig_tick(self)
    ui_anim._Ticker._tick = w_tick

    orig_adv = ui_anim.Tween.advance
    def w_adv(self, now):
        if last["adv"] is not None: gaps["adv"].append((now - last["adv"]) * 1000)
        last["adv"] = now
        return orig_adv(self, now)
    ui_anim.Tween.advance = w_adv

    orig_frame = MainWindow._on_win_geo_frame
    def w_frame(self, t):
        now = time.perf_counter()
        if last["frame"] is not None: gaps["frame"].append((now - last["frame"]) * 1000)
        last["frame"] = now
        return orig_frame(self, t)
    MainWindow._on_win_geo_frame = w_frame

    # 逮住 geo tween 本体看参数
    orig_morph = MainWindow._win_geo_morph
    def w_morph(self, s, e, d=260):
        orig_morph(self, s, e, d)
        tw = self._win_geo_tween
        print(f"morph: duration={tw.duration} min_interval={tw._min_interval} "
              f"from={s.width()}x{s.height()} to={e.width()}x{e.height()}")
    MainWindow._win_geo_morph = w_morph

    window = MainWindow()
    window._port_watch.stop()
    scr = app.primaryScreen().availableGeometry()
    window.setGeometry(scr.x() + 200, scr.y() + 100, 1134, 820)
    window.show()

    def at(ms, name, fn):
        def fire():
            gaps["frame"].clear(); gaps["tick"].clear()
            print(f"--- {name}")
            fn()
        QTimer.singleShot(ms, fire)
    def stat(g):
        g = g[2:]
        return f"n={len(g)} med={statistics.median(g):.1f} max={max(g):.1f}" if g else "n=0"
    def _quit():
        print(f"max#1   frame {stat(gaps['frame'])}  tick {stat(gaps['tick'])}")
        app.quit()
    def max1():
        orig = window.startup_fullscreen
        window.startup_fullscreen()
        print(f"max#1   frame {stat(gaps['frame'])}  tick {stat(gaps['tick'])}")
    at(800, "max#1", max1)
    at(1800, "restore#1", window._on_escape)
    at(2600, "quit", _quit)
    app.exec()
    st2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st2.allKeys()) - set(_SNAP): st2.remove(k)
    for k, v in _SNAP.items(): st2.setValue(k, v)
    st2.sync()
    return 0

if __name__ == "__main__":
    sys.exit(main())
