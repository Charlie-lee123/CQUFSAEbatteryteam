# -*- coding: utf-8 -*-
r"""设置面板残影取证 v2：200% DPI + 真实车号切换 + **屏幕抓图**。

用临时 8 模组车与 E74(5) 互切，触发 has_old=True 的让位/模糊动画；
每步都 `screen.grabWindow(0)`（含 `_SettingsShot` 那种独立顶层窗），
并 dump 覆盖层/真实行/可见性状态到 log。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QPoint, QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

OUT = os.environ.get("GHOST_OUT") or os.path.join(_ROOT, "_check", "settings_ghost2")
os.makedirs(OUT, exist_ok=True)
_SNAP: dict = {}
TAG = "T8-2026"


def run() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(90, exit=True)
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAP.update({k: st.value(k) for k in st.allKeys()})

    import customization
    import ui_anim
    import ui_settings
    from main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    scr = app.primaryScreen()
    sg = scr.geometry()

    if TAG not in customization.vehicle_names():
        customization.save_vehicle(TAG, [25] * 8, [8] * 8)
        print(f"created temp car {TAG} (8 modules)", flush=True)

    window = MainWindow()
    window._port_watch.stop()
    window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    window.setGeometry(sg.x() + (sg.width() - 1134) // 2,
                       sg.y() + max(20, (sg.height() - 820) // 4), 1134, 820)
    window.show()
    window.raise_()
    window.activateWindow()

    def shot(name):
        try:
            scr.grabWindow(0).save(os.path.join(OUT, name))
            print(f"shot {name}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"shot err {exc!r}", flush=True)

    def dump(tag):
        d = window._settings_dialog
        if d is None:
            return
        try:
            ov = d._veh_overlay()
            sh = getattr(window, "_settings_shot", None)
            lr = d._module_rows_rect()
            print(f"[{tag}] overlay_visible={ov.isVisible() if ov else None} "
                  f"ov_geom={ov.geometry() if ov else None} "
                  f"live_rows={lr if not lr.isNull() else None} "
                  f"rows={len(d._module_rows)} "
                  f"shot_visible={sh.isVisible() if sh else None} "
                  f"shot_alpha={getattr(sh,'_alpha',None) if sh else None}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[{tag}] dump err {exc!r}", flush=True)

    def _select(name):
        d = window._settings_dialog
        if d is None:
            return
        idx = d.cmb_vehicle_number.findData(name)
        print(f"select {name!r} idx={idx}", flush=True)
        if idx >= 0:
            d.cmb_vehicle_number.setCurrentIndex(idx)

    def step_open():
        window._open_settings()
        print("opened", flush=True)

    def step_switch1():
        dump("after_flyout")
        shot("01_after_flyout.png")
        _select(TAG)                      # E74(5) -> T8(8)
        # ⛔ 关键：只等 260ms（动画 780ms 未完）就切回 → 精确复现「打断」
        QTimer.singleShot(260, step_interrupt)

    def step_interrupt():
        dump("mid_5to8_interrupt")
        shot("02_mid_5to8.png")
        _select("E74-2026")               # 打断 5->8，立即 8->5
        QTimer.singleShot(1600, step_after)

    def step_after():
        dump("after_interrupt_settled")
        shot("03_after_interrupt.png")
        # 滚到主题/材质强度区再抓
        d = window._settings_dialog
        if d is not None:
            sa = d.findChild(QScrollArea)
            if sa is not None:
                sa.verticalScrollBar().setValue(400)
        QTimer.singleShot(600, step_scrolled)

    def step_scrolled():
        dump("scrolled400")
        shot("04_scrolled400.png")
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
        print("done", flush=True)
        os._exit(0)

    QTimer.singleShot(1200, step_open)
    QTimer.singleShot(2400, step_switch1)
    QTimer.singleShot(7000, _quit)
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
