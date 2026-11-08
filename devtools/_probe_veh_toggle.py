# -*- coding: utf-8 -*-
r"""验证「0↔5 塌陷」自愈：快速在 未选择↔E74 之间反复切换，最后必须回到 5 行，
且面板屏幕合成图 == 自绘图（无残影）。

最大化窗口（贴合用户 startup_maximized），真实 200% DPI，多时机屏幕-vs-控件差分。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

import numpy as np  # noqa: E402
from PySide6.QtCore import QPoint, QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = os.environ.get("GHOST_OUT") or os.path.join(_ROOT, "_check", "veh_toggle")
os.makedirs(OUT, exist_ok=True)
_SNAP: dict = {}


def run() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(90, exit=True)
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAP.update({k: st.value(k) for k in st.allKeys()})

    import ui_anim
    from main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    scr = app.primaryScreen()
    results = []

    window = MainWindow()
    window._port_watch.stop()
    window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    window.showMaximized()
    window.raise_()
    window.activateWindow()

    def ghost_diff(tag: str) -> None:
        d = window._settings_dialog
        if d is None:
            return
        try:
            QApplication.processEvents()
            tl = d.mapToGlobal(QPoint(0, 0)); sz = d.size()
            dpr = scr.devicePixelRatio()
            px = int(round(tl.x() * dpr)); py = int(round(tl.y() * dpr))
            pw = int(round(sz.width() * dpr)); ph = int(round(sz.height() * dpr))
            from PIL import ImageGrab
            a = np.array(ImageGrab.grab(bbox=(px, py, px + pw, py + ph),
                                        all_screens=True).convert("RGB")).astype(np.int16)
            dg = d.grab()
            from PySide6.QtGui import QImage
            img = dg.toImage().convertToFormat(QImage.Format.Format_RGB32)
            w, h = img.width(), img.height()
            arr = np.frombuffer(img.constBits(), np.uint8)
            arr = arr[: img.bytesPerLine() * h].reshape(h, img.bytesPerLine() // 4, 4)[:, :w, :]
            b = arr[:, :, [2, 1, 0]].astype(np.int16)   # BGR->RGB
            hh = min(a.shape[0], b.shape[0]); ww = min(a.shape[1], b.shape[1])
            dmax = np.abs(a[:hh, :ww] - b[:hh, :ww]).max(axis=2)
            big = float((dmax > 40).mean()) * 100.0
            results.append((tag, big))
            a_img = ImageGrab.grab(bbox=(px, py, px + pw, py + ph), all_screens=True)
            a_img.save(os.path.join(OUT, f"{tag}_screen.png"))
            print(f"[{tag}] ghost big%={big:.2f}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[{tag}] err {exc!r}", flush=True)

    def step_open():
        window._open_settings()
        print("opened", flush=True)

    def step_toggle():
        d = window._settings_dialog
        if d is None:
            print("no dialog", flush=True)
            return
        combo = d.cmb_vehicle_number
        # 找 未选择(index 0 / data "") 与 E74 的下标
        i_none = combo.findData("")
        i_e74 = combo.findData("E74-2026")
        print(f"i_none={i_none} i_e74={i_e74} start_rows={len(d._module_rows)}", flush=True)
        seq = [i_none, i_e74, i_none, i_e74, i_none, i_e74]
        for idx in seq:
            if idx >= 0:
                combo.setCurrentIndex(idx)
        print("toggle storm done (immediately)", flush=True)

    def step_check():
        d = window._settings_dialog
        if d is None:
            print("no dialog at check", flush=True)
            _quit(); return
        n = len(d._module_rows)
        print(f"AFTER STORM rows={n} spin={d.spin_vehicle_modules.value()} "
              f"-> {'OK ✓' if n == 5 else 'BAD ✗'}", flush=True)
        ghost_diff("01_after_storm")
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
        print("=== SUMMARY ===", flush=True)
        for tag, big in results:
            print(f"  {tag}: ghost big%={big:.2f}", flush=True)
        os._exit(0)

    QTimer.singleShot(1200, step_open)
    QTimer.singleShot(2400, step_toggle)
    QTimer.singleShot(4500, step_check)
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
