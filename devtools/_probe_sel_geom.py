# -*- coding: utf-8 -*-
"""车号 select 方向（has_old=False）几何取证：每帧记录 rect_new / holder / overlay /
theme / scroll 的真实坐标，判定覆盖层为何溢出到 主题/材质强度。

直接驱动 _veh_start([25]*5,[8]*5)（先清空模组行 → 无旧块），绕开 combo/主窗口噪声。
产物：_check/sel_geom.json
"""
import os
import sys
import json
import time
import faulthandler

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
faulthandler.dump_traceback_later(60, exit=True)

from PySide6.QtCore import QSettings, Qt, QTimer, QRect
from PySide6.QtWidgets import QApplication, QScrollArea

st = QSettings("MimoWorks", "SerialAssistant")
_SNAP = {k: st.value(k) for k in st.allKeys()}

import customization
app = QApplication.instance() or QApplication(sys.argv)
import ui_anim
import ui_settings
from main_window import MainWindow

ui_anim.set_frame_hz(ui_anim.display_max_hz())
scr = app.primaryScreen()
sg = scr.geometry()

if not customization.vehicle_names():
    customization.save_vehicle("E74-2026", [25] * 5, [8] * 5)
car = customization.vehicle_names()[0]

window = MainWindow()
window._port_watch.stop()
w, h = 1134, 820
window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
window.setGeometry(sg.x() + (sg.width() - w) // 2,
                   sg.y() + max(60, (sg.height() - h) // 3), w, h)
window.show()
window.raise_()
window.activateWindow()

GEO = []
_orig_tick = ui_settings.SettingsDialog._veh_tick


def _wrap(self, state, k):
    _orig_tick(self, state, k)
    try:
        ms = k * 780.0
        body = self.form.parentWidget()
        hrect = state.get("holder")
        hg = hrect.geometry() if hrect is not None else QRect()
        ov = self._veh_overlay()
        ovg = ov.geometry() if (ov is not None and ov.isVisible()) else QRect()
        vh = getattr(ov, "_visible_h", None)
        rn = state.get("rect_new")
        rnd = [rn.x(), rn.y(), rn.width(), rn.height()] if rn is not None and not rn.isNull() else None
        theme = self.cmb_theme
        tg = theme.geometry()
        sa = self.findChild(QScrollArea)
        sbv = sa.verticalScrollBar().value() if sa is not None else None
        GEO.append({
            "ms": round(ms, 1),
            "rect_new": rnd,
            "holder": [hg.x(), hg.y(), hg.width(), hg.height()],
            "overlay": [ovg.x(), ovg.y(), ovg.width(), ovg.height()],
            "visible_h": vh,
            "theme": [tg.x(), tg.y(), tg.width(), tg.height()],
            "scroll": sbv,
            "body": [body.x(), body.y(), body.width(), body.height()],
        })
    except Exception as exc:  # noqa: BLE001
        GEO.append({"err": repr(exc)})


ui_settings.SettingsDialog._veh_tick = _wrap


def _open():
    window._open_settings()
    d = window._settings_dialog
    d._veh_allow = False
    if not customization.current_vehicle():
        customization.select_vehicle(car)
        d._sync_vehicle_rows()
    else:
        d._sync_vehicle_rows()
    # 清空模组行，模拟「未选车」状态
    d._rebuild_module_rows([], [])
    d._shown_cells = []
    d._shown_temps = []
    d._veh_allow = True
    sa = d.findChild(QScrollArea)
    if sa is not None:
        sa.verticalScrollBar().setValue(0)
    QTimer.singleShot(700, _start)


def _start():
    d = window._settings_dialog
    # has_old=False：当前无模组行 → pix_old=None
    d._veh_start([25] * 5, [8] * 5)
    QTimer.singleShot(1500, _quit)


def _quit():
    out = os.path.join(_ROOT, "_check", "sel_geom.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(GEO, fh, indent=1)
    st2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st2.allKeys()) - set(_SNAP):
        st2.remove(k)
    for k, v in _SNAP.items():
        st2.setValue(k, v)
    st2.sync()
    os._exit(0)


QTimer.singleShot(1000, _open)
wd = QTimer()
wd.setSingleShot(True)
wd.timeout.connect(lambda: os._exit(3))
wd.start(30000)
app.exec()
