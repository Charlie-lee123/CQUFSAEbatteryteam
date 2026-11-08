# -*- coding: utf-8 -*-
r"""接收区展开/收起（E）流畅度录屏取证（2026-11-07 用户「还是感觉卡顿」）。

机械指标：
  · 包装 MainWindow._on_rx_transition：每个 tick 记录 时间戳 + 该帧耗时
    （ms）—— 若单帧 >16.7ms，则 60fps 都跑不满，用户就会"感觉卡顿"。
  · 20ms 采样定时器记录事件循环间隔 dt，>40ms 记为一次冻结。
  · ffmpeg gdigrab 录屏供人工目检。
产物：_check/rx_anim/{rx.json, rx.mp4}
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
OUT = os.path.join(_ROOT, "_check", "rx_anim")
os.makedirs(OUT, exist_ok=True)
_TICKS: list[dict] = []
_LOOP: list[dict] = []
_EVENTS: list[tuple[float, str]] = []


def _log_event(tag: str) -> None:
    _EVENTS.append((_ms(), tag))
    print(f"EVENT {_ms():7.1f} {tag}", flush=True)


def run_app() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(40, exit=True)
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    app = QApplication.instance() or QApplication(sys.argv)
    import ui_anim
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

    _orig = MainWindow._on_rx_transition

    def _wrap(self, t: float):
        t0 = time.perf_counter()
        try:
            return _orig(self, t)
        finally:
            dt = (time.perf_counter() - t0) * 1000.0
            _TICKS.append({"t": _ms(), "k": float(t), "cost": dt})

    MainWindow._on_rx_transition = _wrap

    sampler = QTimer()
    sampler.setInterval(20)

    def _sample():
        now = _ms()
        dt = (now - _LOOP[-1]["t"]) if _LOOP else 0.0
        _LOOP.append({"t": now, "dt": dt})

    sampler.timeout.connect(_sample)
    sampler.start()

    def _expand():
        _log_event("_expand_receive() 调用")
        window._expand_receive()
        _log_event("_expand_receive() 返回")

    def _collapse():
        _log_event("_collapse_receive() 调用")
        window._collapse_receive()
        _log_event("_collapse_receive() 返回")

    def _quit():
        _log_event("quit")
        try:
            import json
            with open(os.path.join(OUT, "rx.json"), "w", encoding="utf-8") as fh:
                json.dump({"ticks": _TICKS, "loop": _LOOP, "events": _EVENTS}, fh)
            print("STAGE: rx.json saved", flush=True)
        except Exception as exc:      # noqa: BLE001
            print(f"STAGE: rx.json 失败 {exc}", flush=True)
        st2 = QSettings("MimoWorks", "SerialAssistant")
        for k in set(st2.allKeys()) - set(_SNAPSHOT):
            st2.remove(k)
        for k, v in _SNAPSHOT.items():
            st2.setValue(k, v)
        st2.sync()
        sys.stdout.flush()
        os._exit(0)

    wd = threading.Timer(25.0, lambda: os._exit(3))
    wd.daemon = True
    wd.start()

    QTimer.singleShot(1200, _expand)
    QTimer.singleShot(3200, _collapse)
    QTimer.singleShot(5200, _quit)
    app.exec()
    wd.cancel()
    return 0


def record() -> str:
    rec = os.path.join(OUT, "rx.mp4")
    if os.path.exists(rec):
        os.remove(rec)
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "gdigrab", "-framerate", "60",
         "-offset_x", "0", "-offset_y", "0",
         "-video_size", "3200x2000", "-i", "desktop", "-t", "12",
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
        out, _ = app.communicate(timeout=35)
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
        sp = os.path.join(OUT, "rx.json")
        if os.path.exists(sp):
            with open(sp, encoding="utf-8") as fh:
                data = json.load(fh)
            ticks = data.get("ticks", [])
            loop = data.get("loop", [])
            print(f"\n=== 接收区转场：{len(ticks)} 个 tick ===")
            if ticks:
                costs = [d["cost"] for d in ticks]
                costs_sorted = sorted(costs)
                p50 = costs_sorted[len(costs_sorted) // 2]
                p95 = costs_sorted[int(len(costs_sorted) * 0.95)]
                print(f"  单帧耗时 ms: min={min(costs):.1f} p50={p50:.1f} "
                      f"p95={p95:.1f} max={max(costs):.1f}")
                print(f"  >16.7ms 的帧数：{sum(1 for c in costs if c > 16.7)}"
                      f" / {len(costs)}")
            gaps = [d["dt"] for d in loop if d["dt"] > 40]
            print(f"\n=== 事件循环：{len(loop)} 个采样，>40ms 冻结 {len(gaps)} 次 ===")
            for d in loop:
                if d["dt"] > 40:
                    print(f"  t={d['t']:7.0f}ms dt={d['dt']:.0f}ms")
    finally:
        try:
            _st2 = QSettings("MimoWorks", "SerialAssistant")
            for _k in set(_st2.allKeys()) - set(_SNAPSHOT):
                _st2.remove(_k)
            for _k, _v in _SNAPSHOT.items():
                _st2.setValue(_k, _v)
            _st2.sync()
        except Exception as _exc:      # noqa: BLE001
            print(f"[warn] QSettings 兜底恢复失败：{_exc}")
    sys.exit(0)
