# -*- coding: utf-8 -*-
r"""「未选择↔选中」两方向动画取证：对比 选中→未选择 与 未选择→选中 的真实观感。

用户问题：为何「从未选择到已有车号」的动画不能做成「从已有车号到未选择」那样？
本脚本忠实复现两个方向，逐帧抓整窗 + 覆盖层自身 PNG，便于肉眼对比两方向的差异。

  - 阶段 0：选中→未选择（deselect，走 has_old=True，旧车块高斯淡出）。
  - 阶段 1：未选择→选中（select，走 has_old=False，占位推开 + 新车块淡入）。

QSettings 全程快照 + 退出还原（含临时造的车）。
产物：_check/vehicle_seldesel/{base,tick_p0_*,tick_p1_*,ov_p0_*,ov_p1_*,settled}.png + events.json
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

_T0 = time.perf_counter()


def _ms() -> float:
    return (time.perf_counter() - _T0) * 1000.0


OUT = os.path.join(_ROOT, "_check", "vehicle_seldesel")
os.makedirs(OUT, exist_ok=True)
_SNAPSHOT: dict = {}
_EVENTS: list[tuple[float, str]] = []
_GRAB_N = [0]
_PHASE = [0]
_GEO = []


def _log(tag: str) -> None:
    _EVENTS.append((_ms(), tag))
    print(f"EVENT {_ms():7.1f} {tag}", flush=True)


def run_app() -> int:
    import json
    import faulthandler
    faulthandler.dump_traceback_later(90, exit=True)
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
    if not names:
        customization.save_vehicle("E74-2026", [25] * 5, [8] * 5)
        _log("临时造车 E74-2026（5 模组）")
    # 第二台车：模组数不同（9），专门压「换车号且改模组数」的 has_old=True 路径
    if "E22-2026" not in customization.vehicle_names():
        customization.save_vehicle("E22-2026", [25] * 9, [8] * 9)
        _log("临时造车 E22-2026（9 模组）")
    car_name = "E74-2026"
    car_name2 = "E22-2026"

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
        _orig_tick(self, state, k)
        try:
            ms = k * 780.0
            phase = _PHASE[0]
            sa = self.findChild(QScrollArea)
            ov = self._veh_overlay()

            def _viewport_grab():
                if sa is not None and sa.viewport() is not None:
                    return sa.viewport().grab()
                return self.grab()

            # (1) 完整视口（用户实际看到的）
            pix = _viewport_grab()
            _GRAB_N[0] += 1
            pix.save(os.path.join(OUT, f"tick_p{phase}_{int(round(ms)):05d}.png"))
            # (2) 仅覆盖层自身位图（高斯档）
            if ov is not None and ov.isVisible():
                try:
                    ov_pix = ov.grab()
                    ov_pix.save(os.path.join(OUT, f"ov_p{phase}_{int(round(ms)):05d}.png"))
                except Exception:
                    pass
            # (3) 隐藏覆盖层后再抓：看底下**真实行**是否透出来（双份残影判定）
            hidden_real = None
            if ov is not None:
                try:
                    ov.hide()
                    ov.update()
                    hidden_real = _viewport_grab()
                    hidden_real.save(os.path.join(OUT, f"real_p{phase}_{int(round(ms)):05d}.png"))
                except Exception:
                    pass
                finally:
                    try:
                        ov.show()
                        ov.update()
                    except Exception:
                        pass
            # —— 几何取证：每帧记录 rect_new/holder/overlay/theme/scroll ——
            try:
                body = self.form.parentWidget()
                hrect = state.get("holder")
                hg = hrect.geometry() if hrect is not None else None
                ovg = ov.geometry() if (ov is not None and ov.isVisible()) else None
                vh = getattr(ov, "_visible_h", None)
                rn = state.get("rect_new")
                th = self.cmb_theme.geometry()
                sbv = sa.verticalScrollBar().value() if sa is not None else None
                _GEO.append({
                    "phase": phase, "ms": round(ms, 1),
                    "rect_new": [rn.x(), rn.y(), rn.width(), rn.height()] if rn and not rn.isNull() else None,
                    "holder": [hg.x(), hg.y(), hg.width(), hg.height()] if hg else None,
                    "overlay": [ovg.x(), ovg.y(), ovg.width(), ovg.height()] if ovg else None,
                    "visible_h": vh,
                    "theme": [th.x(), th.y(), th.width(), th.height()],
                    "scroll": sbv,
                    "real_has_module": (hidden_real is not None),
                })
            except Exception as _ge:
                _GEO.append({"err": repr(_ge)})
        except Exception as exc:  # noqa: BLE001
            print(f"grab err {exc!r}", flush=True)

    ui_settings.SettingsDialog._veh_tick = _wrap_tick

    def _scroll_to_module(d):
        try:
            sa = d.findChild(QScrollArea)
            if sa is not None:
                rect = d._module_rows_rect()
                sb = sa.verticalScrollBar()
                sb.setValue(max(0, int(rect.y()) - 120))
        except Exception:
            pass

    def _open():
        _log("_open_settings()")
        window._open_settings()
        d = window._settings_dialog
        _state["dialog"] = d
        _log(f"dialog={d is not None}")
        # 强制进入「静态选中车」状态（不带动画），作为基准。
        d._veh_allow = False
        customization.select_vehicle(car_name)
        d._sync_vehicle_rows()
        _log(f"强制选中 {car_name}（静态，无动画）")
        d._veh_allow = True
        _scroll_to_module(d)
        try:
            sa = d.findChild(QScrollArea)
            pix = sa.viewport().grab() if sa is not None else d.grab()
            pix.save(os.path.join(OUT, "base.png"))
            _log("base.png saved")
        except Exception as exc:
            _log(f"base grab err {exc!r}")

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
        _log(f"switch[{phase}] 触发（动画开始）")

    def _quit():
        d = _state["dialog"]
        if d is not None:
            try:
                d.grab().save(os.path.join(OUT, "settled.png"))
                _log("settled.png saved")
            except Exception:
                pass
        try:
            with open(os.path.join(OUT, "events.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"events": [[t, e] for t, e in _EVENTS],
                           "grabs": _GRAB_N[0]}, fh)
            with open(os.path.join(OUT, "geom.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(_GEO, fh)
        except Exception as exc:
            print(f"events.json err {exc!r}", flush=True)
        st2 = QSettings("MimoWorks", "SerialAssistant")
        for k in set(st2.allKeys()) - set(_SNAPSHOT):
            st2.remove(k)
        for k, v in _SNAPSHOT.items():
            st2.setValue(k, v)
        st2.sync()
        _log("settings restored")
        print(f"STAGE: done grabs={_GRAB_N[0]}", flush=True)
        sys.stdout.flush()
        os._exit(0)

    wd = QTimer()
    wd.setSingleShot(True)
    wd.timeout.connect(lambda: os._exit(3))
    wd.start(45000)

    QTimer.singleShot(1000, _open)
    QTimer.singleShot(2800, lambda: _switch(car_name2, 1, f"{car_name}→{car_name2}(5→9)"))
    QTimer.singleShot(6200, lambda: _switch(car_name, 2, f"{car_name2}→{car_name}(9→5)"))
    QTimer.singleShot(9600, lambda: _switch("", 3, f"{car_name}→(未选择)"))
    QTimer.singleShot(13000, lambda: _switch(car_name, 4, f"(未选择)→{car_name}"))
    QTimer.singleShot(16200, _quit)
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
            print("[兜底] QSettings 已恢复", flush=True)
        except Exception as _exc:
            print(f"[warn] QSettings 恢复失败：{_exc}", flush=True)
    sys.exit(0)
