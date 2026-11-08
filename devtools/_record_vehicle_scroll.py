# -*- coding: utf-8 -*-
r"""车号动画「滚动 + 真实模组数」取证：复现用户真实场景（模块区滚动、非 100% 场景）。

与 _record_vehicle_seldesel_switch 的区别：
  - 模组数大（18 / 5），模块区**必须滚动**才装得下；
  - 打开后把模块区滚动到中段（不归零），再切车号，抓整窗 + 覆盖层 + 隐藏覆盖层底图；
  - geom 统一到对话框坐标（mapTo）。
产物：_check/vehicle_scroll/{tick_p*,ov_p*,real_p*}.png + geom.json + events.json
"""
import os
import sys
import time
import json

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QPoint, QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

_T0 = time.perf_counter()


def _ms() -> float:
    return (time.perf_counter() - _T0) * 1000.0


OUT = os.path.join(_ROOT, "_check", "vehicle_scroll")
os.makedirs(OUT, exist_ok=True)
_SNAPSHOT: dict = {}
_EVENTS: list = []
_GRAB_N = [0]
_PHASE = [0]
_GEO = []
_SCROLL_TARGET = [0.45]  # 滚动到中段


def _log(tag: str) -> None:
    _EVENTS.append((_ms(), tag))
    print(f"EVENT {_ms():7.1f} {tag}", flush=True)


def run_app() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(120, exit=True)
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    import customization
    app = QApplication.instance() or QApplication(sys.argv)
    import ui_anim
    import ui_settings
    from main_window import MainWindow

    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    scr = app.primaryScreen()
    sg = scr.geometry()

    names = customization.vehicle_names()
    if "E74-2026" not in names:
        customization.save_vehicle("E74-2026", [25] * 18, [8] * 18)
        _log("临时造车 E74-2026（18 模组）")
    if "E22-2026" not in names:
        customization.save_vehicle("E22-2026", [25] * 5, [8] * 5)
        _log("临时造车 E22-2026（5 模组）")
    car_a, car_b = "E74-2026", "E22-2026"

    window = MainWindow()
    window._port_watch.stop()
    w, h = 1134, 820
    window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    window.setGeometry(sg.x() + (sg.width() - w) // 2,
                       sg.y() + max(60, (sg.height() - h) // 3), w, h)
    window.show()
    window.raise_()
    window.activateWindow()

    _state = {"dialog": None}
    _orig_tick = ui_settings.SettingsDialog._veh_tick

    def _wrap_tick(self, state, k):
        try:
            _orig_tick(self, state, k)
        except Exception:
            import traceback
            traceback.print_exc()
            raise
        try:
            ms = k * 780.0
            phase = _PHASE[0]
            sa = self.findChild(QScrollArea)
            ov = self._veh_overlay()
            try:
                pix = self.grab()
                _GRAB_N[0] += 1
                pix.save(os.path.join(OUT, f"tick_p{phase}_{int(round(ms)):05d}.png"))
            except Exception:
                pass
            if ov is not None and ov.isVisible():
                try:
                    ov.grab().save(os.path.join(OUT, f"ov_p{phase}_{int(round(ms)):05d}.png"))
                except Exception:
                    pass
            hidden_real = None
            if ov is not None:
                try:
                    ov.hide(); ov.update()
                    hidden_real = self.grab()
                    hidden_real.save(os.path.join(OUT, f"real_p{phase}_{int(round(ms)):05d}.png"))
                except Exception:
                    pass
                finally:
                    try:
                        ov.show(); ov.update()
                    except Exception:
                        pass
            try:
                hrect = state.get("holder")
                hg = hrect.geometry() if hrect is not None else None
                ovg = ov.geometry() if (ov is not None and ov.isVisible()) else None
                vh = getattr(ov, "_visible_h", None)
                rn = state.get("rect_new")
                th = self.cmb_theme
                _GEO.append({
                    "phase": phase, "ms": round(ms, 1),
                    "rect_new": [rn.x(), rn.y(), rn.width(), rn.height()] if rn and not rn.isNull() else None,
                    "holder": ([hrect.mapTo(self, QPoint(0, 0)).x(), hrect.mapTo(self, QPoint(0, 0)).y(), hg.width(), hg.height()] if hg else None),
                    "overlay": ([ov.mapTo(self, QPoint(0, 0)).x(), ov.mapTo(self, QPoint(0, 0)).y(), ovg.width(), ovg.height()] if ovg else None),
                    "visible_h": vh,
                    "theme": [th.mapTo(self, QPoint(0, 0)).x(), th.mapTo(self, QPoint(0, 0)).y(), th.width(), th.height()],
                    "scroll": (sa.verticalScrollBar().value() if sa is not None else None),
                    "scroll_max": (sa.verticalScrollBar().maximum() if sa is not None else None),
                    "real_has_module": (hidden_real is not None),
                })
            except Exception as _ge:
                _GEO.append({"err": repr(_ge)})
        except Exception as exc:
            print(f"grab err {exc!r}", flush=True)

    ui_settings.SettingsDialog._veh_tick = _wrap_tick

    def _open():
        window._open_settings()
        d = window._settings_dialog
        _state["dialog"] = d
        d._veh_allow = False
        customization.select_vehicle(car_a)
        d._sync_vehicle_rows()
        d._veh_allow = True
        try:
            sa = d.findChild(QScrollArea)
            if sa is not None:
                bar = sa.verticalScrollBar()
                mx = bar.maximum()
                target = int(mx * _SCROLL_TARGET[0])
                bar.setValue(target)
                _log(f"滚动到中段 value={target}/{mx}")
        except Exception as exc:
            _log(f"scroll err {exc!r}")

    def _switch(target, phase, label):
        d = _state["dialog"]
        combo = d.cmb_vehicle_number
        idx = combo.findData(target)
        if idx < 0:
            _log(f"⛔ 找不到 {label!r}")
            return
        _PHASE[0] = phase
        _log(f"switch[{phase}] {label}: setCurrentIndex({idx})")
        combo.setCurrentIndex(idx)

    def _quit():
        d = _state["dialog"]
        if d is not None:
            try:
                d.grab().save(os.path.join(OUT, "settled.png"))
            except Exception:
                pass
        with open(os.path.join(OUT, "events.json"), "w", encoding="utf-8") as fh:
            json.dump({"events": [[t, e] for t, e in _EVENTS], "grabs": _GRAB_N[0]}, fh)
        with open(os.path.join(OUT, "geom.json"), "w", encoding="utf-8") as fh:
            json.dump(_GEO, fh)
        st2 = QSettings("MimoWorks", "SerialAssistant")
        for k in set(st2.allKeys()) - set(_SNAPSHOT):
            st2.remove(k)
        for k, v in _SNAPSHOT.items():
            st2.setValue(k, v)
        st2.sync()
        print(f"STAGE: done grabs={_GRAB_N[0]}", flush=True)
        os._exit(0)

    wd = QTimer()
    wd.setSingleShot(True)
    wd.timeout.connect(lambda: os._exit(3))
    wd.start(60000)

    QTimer.singleShot(1000, _open)
    QTimer.singleShot(3500, lambda: _switch(car_b, 1, f"{car_a}→{car_b}(18→5)"))
    QTimer.singleShot(7500, lambda: _switch(car_a, 2, f"{car_b}→{car_a}(5→18)"))
    QTimer.singleShot(11500, _quit)
    app.exec()
    return 0


if __name__ == "__main__":
    _st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: _st.value(k) for k in _st.allKeys()})
    try:
        run_app()
    finally:
        try:
            _st2 = QSettings("MimoWorks", "SerialAssistant")
            for _k in set(_st2.allKeys()) - set(_SNAPSHOT):
                _st2.remove(_k)
            for _k, _v in _SNAPSHOT.items():
                _st2.setValue(_k, _v)
            _st2.sync()
        except Exception:
            pass
    sys.exit(0)
