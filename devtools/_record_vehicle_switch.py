# -*- coding: utf-8 -*-
r"""车号/模组数「切换」方向（has_old=True）残影取证（2026-11-07 用户：od64 仍有残影）。

忠实复现**换车号 / 改模组数**触发 _veh_start(has_old=True) 的真实路径：
  1. 打开设置面板（真实 _open_settings）。
  2. 改模组数（spin_vehicle_modules.setValue 到不同值）→ valueChanged →
     _on_vehicle_modules_changed → _sync_vehicle_rows → 结构上不同 → _veh_start。
  3. 每个 _veh_tick 把整个对话框 grab 成 PNG（密集逐拍），文件名带 ms。
  4. 同时把覆盖层自身（透明底）单独 grab，看它到底画了什么、画在哪。

⛔ QSettings 全量快照 + 退出还原。产物：_check/vehicle_switch/{base,tick_*,settled}.png
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


OUT = os.path.join(_ROOT, "_check", "vehicle_switch")
os.makedirs(OUT, exist_ok=True)
_SNAPSHOT: dict = {}
_EVENTS: list[tuple[float, str]] = []
_GRAB_N = [0]


def _log(tag: str) -> None:
    _EVENTS.append((_ms(), tag))
    print(f"EVENT {_ms():7.1f} {tag}", flush=True)


def run_app() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(60, exit=True)
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    app = QApplication.instance() or QApplication(sys.argv)
    import ui_anim
    import ui_settings
    from main_window import MainWindow

    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    scr = app.primaryScreen()
    sg = scr.geometry()

    window = MainWindow()
    window._port_watch.stop()
    w, h = 1134, 820
    window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    window.setGeometry(sg.x() + (sg.width() - w) // 2,
                       sg.y() + max(60, (sg.height() - h) // 3), w, h)
    window.show()
    window.raise_()
    window.activateWindow()

    import customization
    _state = {"dialog": None}

    # 包装 _veh_tick：每个 tick 抓整窗 + 覆盖层自身
    _orig_tick = ui_settings.SettingsDialog._veh_tick

    def _wrap_tick(self, state, k):
        _orig_tick(self, state, k)
        try:
            ms = k * 780.0
            sa = self.findChild(QScrollArea)
            if sa is not None and sa.viewport() is not None:
                pix = sa.viewport().grab()
            else:
                pix = self.grab()
            _GRAB_N[0] += 1
            pix.save(os.path.join(OUT, f"tick_{int(round(ms)):05d}.png"))
            # 覆盖层自身（透明底）
            o = self._veh_overlay()
            if o is not None and o.isVisible():
                try:
                    op = o.grab()
                    op.save(os.path.join(OUT, f"ov_{int(round(ms)):05d}.png"))
                except Exception:
                    pass
        except Exception as exc:  # noqa: BLE001
            print(f"grab err {exc!r}", flush=True)

    ui_settings.SettingsDialog._veh_tick = _wrap_tick

    def _open():
        _log("_open_settings()")
        window._open_settings()
        d = window._settings_dialog
        _state["dialog"] = d
        _log(f"dialog={d is not None}")
        # 把设置面板滚到「模组区 + 下方设置项」都在视口内，复现用户真机看到的重叠
        try:
            sa = d.findChild(QScrollArea)
            if sa is not None:
                rect = d._module_rows_rect()
                sb = sa.verticalScrollBar()
                # 模组区顶 ~120px 处开始显示，露出其下方 主题/材质强度 等设置项
                target = max(0, int(rect.y()) - 120)
                sb.setValue(target)
                _log(f"scrolled to {target} (module y={rect.y()})")
        except Exception as exc:
            _log(f"scroll err {exc!r}")
        # BASE 帧（动作前，已滚动到含下方设置项）
        try:
            sa = d.findChild(QScrollArea)
            pix = sa.viewport().grab() if sa is not None else d.grab()
            pix.save(os.path.join(OUT, "base.png"))
            _log("base.png saved")
        except Exception as exc:
            _log(f"base grab err {exc!r}")

    def _switch():
        d = _state["dialog"]
        if d is None:
            _log("⛔ 无 dialog")
            return
        spin = d.spin_vehicle_modules
        cur = spin.value()
        target = (cur - 2) if cur > 3 else (cur + 2)
        target = max(1, min(20, target))
        _log(f"spin_vehicle_modules.setValue({target})  (当前 cur={cur})")
        spin.setValue(target)   # 触发 valueChanged → _veh_start(has_old=True)
        _log("setValue 返回（动画开始）")

    def _quit():
        d = _state["dialog"]
        if d is not None:
            try:
                d.grab().save(os.path.join(OUT, "settled.png"))
                _log("settled.png saved")
            except Exception:
                pass
        try:
            import json
            with open(os.path.join(OUT, "events.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"events": [[t, e] for t, e in _EVENTS],
                           "grabs": _GRAB_N[0]}, fh)
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
    wd.start(30000)

    QTimer.singleShot(1000, _open)
    QTimer.singleShot(2600, _switch)
    QTimer.singleShot(6000, _quit)
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
