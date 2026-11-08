# -*- coding: utf-8 -*-
r"""车号**切换**（_on_vehicle_picked → _veh_start(has_old=True)）残影取证。

用户原话「车号动画还是有问题 / 还是有残影」。本脚本忠实复现**换车号**触发
_veh_start(has_old=True) 的真实路径（与改模组数是同一套 _veh_paint，但触发源
是 cmb_vehicle_number.currentIndexChanged → _on_vehicle_picked）：

  1. 运行时临时造一台与当前车**模组数不同**的第 2 台车（如 A=5 模组、B=3 模组），
     QSettings 全程快照+还原（含 custom/* 车辆档案键），退出即抹掉临时车。
  2. 打开设置面板（真实 _open_settings），记录 base（A 车 5 模组）。
  3. 切车号到 B（A→B，5→3 缩）：逐拍 grab 整窗 + 覆盖层自身。
  4. 在 B 车上改模组数 3→5（增）：再抓一遍。
  5. 退出还原。

产物：_check/vehicle_carswitch/{base,tick_*,ov_*,settled}*.png + events.json
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


OUT = os.path.join(_ROOT, "_check", "vehicle_carswitch")
os.makedirs(OUT, exist_ok=True)
_SNAPSHOT: dict = {}
_EVENTS: list[tuple[float, str]] = []
_GRAB_N = [0]
_PHASE = [0]


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

    # ---- 临时造第 2 台车（与当前车模组数不同）----
    names = customization.vehicle_names()
    cur = customization.current_vehicle()
    _log(f"现有车辆={names} 当前={cur!r}")
    a_name = cur or (names[0] if names else "")
    # A 车模组数（默认 5）
    a_profile = customization.active_profile()
    a_n = len(a_profile[0]) if a_profile else 5
    b_n = 3 if a_n != 3 else 5  # 保证与 A 不同
    b_name = "E75-2026" if a_name != "E75-2026" else "E73-2026"
    if b_name not in names:
        customization.save_vehicle(
            b_name, [25] * b_n, [8] * b_n)
        _log(f"临时造第2台车 {b_name} 模组数={b_n}")
    # 确保当前车是 A（base 帧反映的是 A）
    if cur != a_name and a_name:
        customization.select_vehicle(a_name)
        _log(f"切回当前车={a_name}")

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
            sa = self.findChild(QScrollArea)
            if sa is not None and sa.viewport() is not None:
                pix = sa.viewport().grab()
            else:
                pix = self.grab()
            _GRAB_N[0] += 1
            phase = _PHASE[0]
            pix.save(os.path.join(OUT, f"tick_p{phase}_{int(round(ms)):05d}.png"))
            o = self._veh_overlay()
            if o is not None and o.isVisible():
                try:
                    op = o.grab()
                    op.save(os.path.join(OUT, f"ov_p{phase}_{int(round(ms)):05d}.png"))
                except Exception:
                    pass
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
                return rect.y()
        except Exception:
            return 0
        return 0

    def _open():
        _log("_open_settings()")
        window._open_settings()
        d = window._settings_dialog
        _state["dialog"] = d
        _log(f"dialog={d is not None}")
        y = _scroll_to_module(d)
        _log(f"scrolled (module y={y})")
        try:
            sa = d.findChild(QScrollArea)
            pix = sa.viewport().grab() if sa is not None else d.grab()
            pix.save(os.path.join(OUT, "base.png"))
            _log("base.png saved")
        except Exception as exc:
            _log(f"base grab err {exc!r}")

    def _pick_car(target_name):
        d = _state["dialog"]
        if d is None:
            _log("⛔ 无 dialog")
            return
        combo = d.cmb_vehicle_number
        idx = combo.findData(target_name)
        if idx < 0:
            _log(f"⛔ 找不到车 {target_name}（combo items={[combo.itemData(i) for i in range(combo.count())]}）")
            return
        cur_idx = combo.currentIndex()
        _log(f"cmb_vehicle_number.setCurrentIndex({idx}) (当前 idx={cur_idx}, {combo.currentData()!r}→{target_name!r})")
        combo.setCurrentIndex(idx)   # 触发 currentIndexChanged → _on_vehicle_picked → _veh_start
        _log("setCurrentIndex 返回（动画开始）")

    def _change_modules(target):
        d = _state["dialog"]
        if d is None:
            _log("⛔ 无 dialog")
            return
        spin = d.spin_vehicle_modules
        cur = spin.value()
        _log(f"spin_vehicle_modules.setValue({target}) (当前 cur={cur})")
        spin.setValue(target)
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
    wd.start(45000)

    QTimer.singleShot(1000, _open)
    # 阶段 0：切车号 A→B（5→3 缩，正是用户投诉的「车号动画」）
    QTimer.singleShot(2800, lambda: (_PHASE.__setitem__(0, 0), _pick_car(b_name)))
    # 阶段 1：在 B 车上改模组数 3→5（增，验证 visible_h 裁剪不外溢）
    QTimer.singleShot(5600, lambda: (_PHASE.__setitem__(0, 1), _change_modules(5)))
    QTimer.singleShot(9000, _quit)
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
