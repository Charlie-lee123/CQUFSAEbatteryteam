# -*- coding: utf-8 -*-
r"""验证 od77：车号下拉改用 `activated` 后，**打开设置不再自激塌陷**。

流程：开窗(最大化) → 打开设置 → 连续采样 2.5s 的行数（必须一直是 5，不能出现 0）
→ 用 activated 模拟用户切到 T9(7) → 采样行数应为 7 → 再切回 E74(5) → 应为 5。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = os.environ.get("GHOST_OUT") or os.path.join(_ROOT, "_check", "veh_activated")
os.makedirs(OUT, exist_ok=True)
_SNAP: dict = {}
_samples: list = []


def run() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(90, exit=True)
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAP.update({k: st.value(k) for k in st.allKeys()})

    import customization
    import ui_anim
    from main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    if "T9-2026" not in customization.vehicle_names():
        customization.save_vehicle("T9-2026", [25] * 7, [8] * 7)

    window = MainWindow()
    window._port_watch.stop()
    window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    window.showMaximized()
    window.raise_(); window.activateWindow()

    def rows():
        d = window._settings_dialog
        return len(d._module_rows) if d is not None else -1

    def open_():
        window._open_settings(); print("opened", flush=True)

    # 打开后连续采样 10 次（每 250ms），必须恒为 5
    def sample(i=0):
        n = rows()
        _samples.append((i * 250, n))
        print(f"  t={i*250:4d}ms rows={n}", flush=True)
        if i < 9:
            QTimer.singleShot(250, lambda: sample(i + 1))
        else:
            QTimer.singleShot(100, user_switch)

    def _activate(name):
        d = window._settings_dialog
        idx = d.cmb_vehicle_number.findData(name)
        print(f"activate {name!r} idx={idx}", flush=True)
        if idx >= 0:
            d.cmb_vehicle_number.setCurrentIndex(idx)
            d.cmb_vehicle_number.activated.emit(idx)   # 模拟用户真正选中

    def user_switch():
        _activate("T9-2026")
        QTimer.singleShot(900, lambda: check("to_T9", 7))

    def check(tag, expect):
        n = rows()
        ok = (n == expect)
        print(f"RESULT {tag}: rows={n} expect={expect} -> {'OK ✓' if ok else 'BAD ✗'}", flush=True)
        if tag == "to_T9":
            _activate("E74-2026")
            QTimer.singleShot(900, lambda: check("back_E74", 5))
        else:
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
        for k, v in _SNAP.items():
            st2.setValue(k, v)
        st2.sync()
        zeros = [t for t, n in _samples if n == 0]
        print("=== SUMMARY ===", flush=True)
        print(f"  open-samples={len(_samples)} zero-frames={zeros if zeros else '无 ✓'}", flush=True)
        os._exit(0)

    QTimer.singleShot(1200, open_)
    QTimer.singleShot(2600, lambda: sample(0))
    QTimer.singleShot(8000, _quit)
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
