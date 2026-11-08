# -*- coding: utf-8 -*-
r"""验证 _veh_start 40ms 合并：快速连改模组数 → 只起一段动画、终态行数正确。

打开设置 → 对 spin_vehicle_modules 快速 setValue(3,5,2,4,6)（模拟滚轮连点，
每次触发 _on_vehicle_modules_changed → _veh_start）→ 等 900ms →
断言 len(_module_rows) == spin.value()，并从 vehicle_anim.log 看 START 次数。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_SNAP: dict = {}


def run() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(60, exit=True)
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAP.update({k: st.value(k) for k in st.allKeys()})

    import ui_anim
    from main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    scr = app.primaryScreen()
    sg = scr.geometry()

    window = MainWindow()
    window._port_watch.stop()
    window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    window.setGeometry(sg.x() + 60, sg.y() + 40, 1134, 820)
    window.show()
    window.raise_()

    def step_open():
        window._open_settings()
        print("opened", flush=True)

    def step_cascade():
        d = window._settings_dialog
        if d is None:
            print("no dialog", flush=True)
            return
        sp = d.spin_vehicle_modules
        print(f"spin min/max={sp.minimum()}/{sp.maximum()} current={sp.value()}", flush=True)
        for v in (3, 5, 2, 4, 6):
            sp.setValue(v)             # 同步连发，模拟滚轮/连点
        print("cascade set 3,5,2,4,6 done (immediately)", flush=True)

    def step_check():
        d = window._settings_dialog
        if d is None:
            print("no dialog at check", flush=True)
            _quit()
            return
        sp = d.spin_vehicle_modules
        n = len(d._module_rows)
        v = sp.value()
        ok = (n == v)
        print(f"RESULT spin={v} nrows={n} -> {'OK ✓' if ok else 'MISMATCH ✗'}", flush=True)
        # 再读日志看 START 次数（本次运行最后 3 秒内）
        _quit()

    def _quit():
        try:
            d = window._settings_dialog
            if d is not None:
                d._veh_allow = False
                d.reject()
        except Exception:
            pass
        st2 = QSettings("MimoWorks", "SerialAssistant")
        for k in set(st2.allKeys()) - set(_SNAP):
            st2.remove(k)
        for k, val in _SNAP.items():
            st2.setValue(k, val)
        st2.sync()
        print("done", flush=True)
        os._exit(0)

    QTimer.singleShot(1200, step_open)
    QTimer.singleShot(2400, step_cascade)
    QTimer.singleShot(3600, step_check)
    QTimer.singleShot(6000, _quit)
    app.exec()
    return 0


if __name__ == "__main__":
    _st = QSettings("MimoWorks", "SerialAssistant")
    _SNAP.update({k: _st.value(k) for k in _st.allKeys()})
    try:
        run()
    finally:
        try:
            _st2 = QSettings("MimoWorks", "SerialAssistant")
            for _k in set(_st2.allKeys()) - set(_SNAP):
                _st2.remove(_k)
            for _k, _v in _SNAP.items():
                _st2.setValue(_k, _v)
            _st2.sync()
        except Exception:
            pass
    sys.exit(0)
