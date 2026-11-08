# -*- coding: utf-8 -*-
r"""车号选择动画（C）录屏取证（2026-11-07 用户「还是没有改善」）。

复现「无数据 → 有数据」方向（用户点名：这个方向参数块没有高斯模糊淡入）：
  1. 打开设置面板（走真实 _open_settings）。
  2. 强制置空 `customization.select_vehicle("")` + `_sync_vehicle_rows()` → 无模组行。
  3. 调 `dialog._veh_start([20,16,12],[8,6,4])` —— 与 _sync_vehicle_rows 里
     选车号后触发的**同一条渲染管线**（新行先 reserve 隐藏 → P0 让位下移 →
     P3 新块高斯模糊淡入）。
  4. 同时包装 _veh_grab_new / _veh_paint 记录 P3 是否有 pix_new 素材、σ 曲线，
     以及每个 tick 的耗时。

⛔ QSettings 全量快照 + 结束还原（customization 也走 QSettings，置空会被还原）。
产物：_check/vehicle_anim/{vehicle.json, vehicle.mp4}
"""
import os
import subprocess
import sys
import threading
import time

_T0 = time.perf_counter()


def _ms() -> float:
    return (time.perf_counter() - _T0) * 1000.0


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_SNAPSHOT: dict = {}
OUT = os.path.join(_ROOT, "_check", "vehicle_anim")
os.makedirs(OUT, exist_ok=True)
_GRAB: list[dict] = []
_PAINT: list[dict] = []
_EVENTS: list[tuple[float, str]] = []


def _log_event(tag: str) -> None:
    _EVENTS.append((_ms(), tag))
    print(f"EVENT {_ms():7.1f} {tag}", flush=True)


def run_app() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(50, exit=True)
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    app = QApplication.instance() or QApplication(sys.argv)
    import ui_anim
    import ui_settings
    from main_window import MainWindow

    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    scr = app.primaryScreen()
    sg = scr.geometry()

    backdrop = QWidget(None, Qt.Window | Qt.FramelessWindowHint
                       | Qt.WindowStaysOnTopHint)
    backdrop.setAttribute(Qt.WA_ShowWithoutActivating)
    backdrop.setAutoFillBackground(True)
    pal = backdrop.palette()
    pal.setColor(backdrop.backgroundRole(), QColor(0, 0, 0))
    backdrop.setPalette(pal)
    backdrop.setWindowFlag(Qt.WindowTransparentForInput, True)
    backdrop.setGeometry(sg)
    backdrop.show()
    backdrop.raise_()

    window = MainWindow()
    window._port_watch.stop()
    w, h = 1134, 820
    window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    window.setGeometry(sg.x() + (sg.width() - w) // 2,
                       sg.y() + max(60, (sg.height() - h) // 3), w, h)
    window.show()
    window.raise_()
    window.activateWindow()

    # 包装 grab_new / paint 取证
    _orig_grab = ui_settings.SettingsDialog._veh_grab_new

    def _wrap_grab(self, state):
        _orig_grab(self, state)
        p = state.get("pix_new")
        r = state.get("rect_new")
        ok = (p is not None and not p.isNull())
        _GRAB.append({
            "t": _ms(), "ok": bool(ok),
            "pix": (f"{p.width()}x{p.height()}" if ok else "null"),
            "rect": (f"{r.width()}x{r.height()}" if r is not None else "None"),
        })
        _log_event(f"_veh_grab_new ok={ok} pix={_GRAB[-1]['pix']} "
                   f"rect={_GRAB[-1]['rect']}")

    ui_settings.SettingsDialog._veh_grab_new = _wrap_grab

    _orig_paint = ui_settings.SettingsDialog._veh_paint

    def _wrap_paint(self, state, ms):
        _orig_paint(self, state, ms)
        try:
            o = self._veh_overlay()
            n = len(getattr(o, "_items", []) or []) if o is not None else 0
        except Exception:  # noqa: BLE001
            n = -1
        _PAINT.append({"t": _ms(), "ms": float(ms), "items": n})

    ui_settings.SettingsDialog._veh_paint = _wrap_paint

    _state = {"dialog": None}

    def _open():
        _log_event("_open_settings() 调用")
        window._open_settings()
        _state["dialog"] = window._settings_dialog
        _log_event(f"_open_settings() 返回 dialog={_state['dialog'] is not None}")

    def _clear():
        d = _state["dialog"]
        if d is None:
            _log_event("⛔ 无 dialog，跳过 clear")
            return
        import customization
        _state["vehicle"] = customization.current_vehicle()
        _log_event(f"强制置空（原 current={_state['vehicle']!r}）")
        customization.select_vehicle("")
        d._sync_vehicle_rows()
        _log_event("置空完成")

    def _start():
        # ⛔ 忠实复现**真实用户路径**：_on_vehicle_picked 就是
        #    customization.select_vehicle(name) + dialog._sync_vehicle_rows()
        #    —— 两者**同步**在同一事件循环拍里跑完，_veh_start 随即被调用
        #    （布局此刻可能还没算过新行几何）。上一版在这里直接调 _veh_start
        #    是从另一个 timer 拍进来的，布局已就绪 → 抓图必然成功，测不出真问题。
        d = _state["dialog"]
        if d is None:
            _log_event("⛔ 无 dialog，跳过")
            return
        import customization
        name = _state.get("vehicle") or customization.current_vehicle()
        _log_event(f"真实路径 select_vehicle({name!r}) + _sync_vehicle_rows()"
                   f"（无数据→有数据）")
        customization.select_vehicle(name)
        d._sync_vehicle_rows()
        _log_event("_sync_vehicle_rows 返回")

    def _quit():
        _log_event("quit")
        try:
            import json
            with open(os.path.join(OUT, "vehicle.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"grab": _GRAB, "paint": _PAINT,
                           "events": _EVENTS}, fh)
            print("STAGE: vehicle.json saved", flush=True)
        except Exception as exc:      # noqa: BLE001
            print(f"STAGE: vehicle.json 失败 {exc}", flush=True)
        st2 = QSettings("MimoWorks", "SerialAssistant")
        for k in set(st2.allKeys()) - set(_SNAPSHOT):
            st2.remove(k)
        for k, v in _SNAPSHOT.items():
            st2.setValue(k, v)
        st2.sync()
        print("STAGE: settings restored", flush=True)
        sys.stdout.flush()
        os._exit(0)

    wd = threading.Timer(30.0, lambda: os._exit(3))
    wd.daemon = True
    wd.start()

    QTimer.singleShot(1000, _open)
    QTimer.singleShot(2600, _clear)
    QTimer.singleShot(4000, _start)
    QTimer.singleShot(5600, _quit)
    app.exec()
    wd.cancel()
    return 0


def record() -> str:
    rec = os.path.join(OUT, "vehicle.mp4")
    if os.path.exists(rec):
        os.remove(rec)
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "gdigrab", "-framerate", "60",
         "-offset_x", "0", "-offset_y", "0",
         "-video_size", "3200x2000", "-i", "desktop", "-t", "14",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", rec],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    app = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--app"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           env=env)
    try:
        out, _ = app.communicate(timeout=40)
        for line in out.decode("utf-8", "replace").splitlines():
            print(line)
    except subprocess.TimeoutExpired:
        app.kill()
        print("⛔ app 子进程超时被杀")
    time.sleep(2.0)
    try:
        ff.stdin.write(b"q")
        ff.stdin.close()
    except Exception:                 # noqa: BLE001
        pass
    try:
        ff.wait(timeout=15)
    except subprocess.TimeoutExpired:
        ff.terminate()
    print("app rc:", app.returncode)
    return rec


if __name__ == "__main__":
    if "--app" in sys.argv:
        run_app()
        os._exit(0)
    _st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: _st.value(k) for k in _st.allKeys()})
    try:
        record()
        import json
        sp = os.path.join(OUT, "vehicle.json")
        if os.path.exists(sp):
            with open(sp, encoding="utf-8") as fh:
                data = json.load(fh)
            gr = data.get("grab", [])
            pt = data.get("paint", [])
            print(f"\n=== _veh_grab_new：{len(gr)} 次 ===")
            for d in gr:
                print(f"  t={d['t']:7.0f}ms ok={d['ok']} pix={d['pix']} "
                      f"rect={d['rect']}")
            print(f"\n=== _veh_paint：{len(pt)} 帧（P3 应有 items 且 ms 320→680）===")
            p3 = [d for d in pt if d["ms"] >= 320]
            for d in p3[::4][:20]:
                print(f"  t={d['t']:7.0f}ms ms={d['ms']:.0f} items={d['items']}")
            if p3:
                withitems = sum(1 for d in p3 if d["items"] > 0)
                print(f"  P3 有素材帧比例：{withitems}/{len(p3)}")
    finally:
        try:
            _st2 = QSettings("MimoWorks", "SerialAssistant")
            for _k in set(_st2.allKeys()) - set(_SNAPSHOT):
                _st2.remove(_k)
            for _k, _v in _SNAPSHOT.items():
                _st2.setValue(_k, _v)
            _st2.sync()
            print("[兜底] QSettings 已按父进程快照恢复")
        except Exception as _exc:      # noqa: BLE001
            print(f"[warn] QSettings 兜底恢复失败：{_exc}")
    sys.exit(0)
