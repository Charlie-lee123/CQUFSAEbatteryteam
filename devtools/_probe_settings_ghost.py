# -*- coding: utf-8 -*-
r"""设置面板「整块残影」取证：`_SettingsShot` 是**独立顶层 ToolTip 窗**，
`dialog.grab()` 抓不到它 —— 必须抓**屏幕**才能看见残影。

流程：开窗 → 打开设置（飞入动画）→ 等飞入结束 → 滚到 主题/材质强度 区
→ **屏幕抓图**保存 → 再滚回顶部 → 屏幕抓图 → 关窗、还原 QSettings。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

OUT = os.environ.get("GHOST_OUT") or os.path.join(_ROOT, "_check", "settings_ghost")
os.makedirs(OUT, exist_ok=True)
_SNAP: dict = {}


def run() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(60, exit=True)
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAP.update({k: st.value(k) for k in st.allKeys()})

    import ui_anim
    import ui_settings
    from main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    scr = app.primaryScreen()
    sg = scr.geometry()

    window = MainWindow()
    window._port_watch.stop()
    window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    window.setGeometry(sg.x() + (sg.width() - 1134) // 2,
                       sg.y() + max(40, (sg.height() - 820) // 3), 1134, 820)
    window.show()
    window.raise_()
    window.activateWindow()

    def shot(name: str) -> None:
        """屏幕抓图（含独立快照窗）。"""
        try:
            pm = scr.grabWindow(0)
            pm.save(os.path.join(OUT, name))
            print(f"shot {name} dpr={pm.devicePixelRatio()}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"shot err {exc!r}", flush=True)

    def step_open() -> None:
        window._open_settings()
        print("opened settings", flush=True)

    def step_after_flyout() -> None:
        # 飞入动画 ~260ms + 34ms 延迟 + 90ms 溶解，留足 900ms 保险
        d = window._settings_dialog
        shot("01_after_flyout.png")
        # 检查快照窗状态
        sh = getattr(window, "_settings_shot", None)
        if sh is not None:
            try:
                print(f"_settings_shot visible={sh.isVisible()} alpha={sh._alpha} geom={sh.geometry()}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"shot state err {exc!r}", flush=True)
        # 滚到主题/材质强度一带
        if d is not None:
            sa = d.findChild(QScrollArea)
            if sa is not None:
                sa.verticalScrollBar().setValue(400)
                print("scrolled 400", flush=True)

    def step_scrolled() -> None:
        shot("02_scrolled400.png")
        sh = getattr(window, "_settings_shot", None)
        if sh is not None:
            try:
                print(f"_settings_shot visible={sh.isVisible()} alpha={sh._alpha}", flush=True)
            except Exception:
                pass

    def step_settle() -> None:
        shot("03_settled.png")
        _quit()

    def _quit() -> None:
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
    QTimer.singleShot(2400, step_after_flyout)
    QTimer.singleShot(3000, step_scrolled)
    QTimer.singleShot(3600, step_settle)
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
