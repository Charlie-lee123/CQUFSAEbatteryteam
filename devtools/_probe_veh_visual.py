# -*- coding: utf-8 -*-
r"""重构后车辆过渡的**视觉**抽查：grow(5→7) 直接到位、shrink(7→5) 占位行补间。

抓屏幕（PIL GDI）存中段帧 + 落定帧，供人眼确认：
  - 模组行始终可见（不再空白、不再快照错位）；
  - shrink 中下方内容平滑上移，无跳变/重影。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PIL import ImageGrab  # noqa: E402
from PySide6.QtCore import QPoint, QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = os.environ.get("GHOST_OUT") or os.path.join(_ROOT, "_check", "veh_visual")
os.makedirs(OUT, exist_ok=True)
_SNAP: dict = {}
TAG = "T9-2026"


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
    scr = app.primaryScreen()
    if TAG not in customization.vehicle_names():
        customization.save_vehicle(TAG, [25] * 7, [8] * 7)

    window = MainWindow()
    window._port_watch.stop()
    window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    window.showMaximized()
    window.raise_(); window.activateWindow()

    def shot(tag):
        d = window._settings_dialog
        if d is None:
            return
        try:
            tl = d.mapToGlobal(QPoint(0, 0)); sz = d.size()
            dpr = scr.devicePixelRatio()
            px = int(round(tl.x() * dpr)); py = int(round(tl.y() * dpr))
            pw = int(round(sz.width() * dpr)); ph = int(round(sz.height() * dpr))
            ImageGrab.grab(bbox=(px, py, px + pw, py + ph), all_screens=True).save(
                os.path.join(OUT, f"{tag}.png"))
            print(f"shot {tag} rows={len(d._module_rows)}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"shot err {exc!r}", flush=True)

    def _sel(name):
        d = window._settings_dialog
        idx = d.cmb_vehicle_number.findData(name) if d else -1
        if d is not None and idx >= 0:
            d.cmb_vehicle_number.setCurrentIndex(idx)
            d.cmb_vehicle_number.activated.emit(idx)   # 模拟用户真正选中

    def s_open():
        window._open_settings(); print("opened", flush=True)

    def s_grow():
        _sel(TAG)                      # E74(5) -> T9(7) grow, 应直接到位
        QTimer.singleShot(600, lambda: shot("01_after_grow"))

    def s_shrink():
        _sel("E74-2026")               # T9(7) -> E74(5) shrink, 补间
        QTimer.singleShot(120, lambda: shot("02_shrink_mid"))
        QTimer.singleShot(700, lambda: shot("03_shrink_settled"))
        QTimer.singleShot(1200, _quit)

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
        print("done", flush=True)
        os._exit(0)

    QTimer.singleShot(1200, s_open)
    QTimer.singleShot(2600, s_grow)
    QTimer.singleShot(3600, s_shrink)
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
