# -*- coding: utf-8 -*-
"""还原动画录屏取证 v2（2026-11-04）：置顶 + 黑垫底 + 亮矩形测窗口 + 关键帧导出。

WorkBuddy 全屏置顶会抢镜 → 垫底窗与主窗测试期临时 WindowStaysOnTopHint。
gdigrab 在 3200x2000@60fps 实测只有 ~16fps → 改 30fps + -t 10。
分析：非黑(>40)包围盒 = 应用矩形（浅色主题 vs 黑垫底，壁纸/深色窗全排除）。
时间线：max@600 → restore@1800 → quit@3000。
"""
import os
import subprocess
import sys
import time

_T0 = time.perf_counter()


def _ms() -> float:
    return (time.perf_counter() - _T0) * 1000.0

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtCore import QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_SNAPSHOT: dict = {}
OUT = os.path.join(_ROOT, "_check", "restore_frames_v2")
os.makedirs(OUT, exist_ok=True)


def main() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(40, exit=True)
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    app = QApplication.instance() or QApplication(sys.argv)
    import ui_anim
    from main_window import MainWindow

    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    print("STAGE: imports done", flush=True)
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
    print("STAGE: window shown", flush=True)

    # 诊断：覆盖层与快照缓存是否就绪
    _orig_build = MainWindow._build_fs_idle_pix

    def _wrap_build(self):
        _orig_build(self)
        p = self._fs_idle_pix
        desc = "null" if p is None or p.isNull() else f"{p.width()}x{p.height()}"
        print(f"DIAG: fs_idle_pix built -> {desc}", flush=True)
        if p is not None and not p.isNull():
            p.save(os.path.join(OUT, "fs_idle_snap.png"))

    MainWindow._build_fs_idle_pix = _wrap_build
    _orig_cover2 = MainWindow._begin_window_cover

    def _wrap_cover2(self, pix, start, *a, **kw):
        r = _orig_cover2(self, pix, start, *a, **kw)
        print(f"DIAG: _begin_window_cover pix="
              f"{'null' if pix is None or pix.isNull() else 'ok'} "
              f"cover={'Y' if r is not None else 'N'}", flush=True)
        return r

    MainWindow._begin_window_cover = _wrap_cover2

    # 全状态采样：30ms 一次，主窗/覆盖层关键状态
    _sampler = QTimer()
    _sampler.setInterval(30)
    _t_app = time.perf_counter()

    def _sample_state():
        c = window._cover_label
        parts = [f"SAMPLE {_ms():7.1f}"]
        try:
            g = window.geometry()
            parts.append(f"win=({g.x()},{g.y()},{g.width()}x{g.height()})"
                         f" op={window.windowOpacity():.2f}"
                         f" fs={window.isFullScreen()}")
        except RuntimeError:
            parts.append("win=dead")
        if c is not None:
            try:
                cg = c.geometry()
                parts.append(f"cover=({cg.x()},{cg.y()},{cg.width()}x{cg.height()})"
                             f" vis={c.isVisible()} op={c.windowOpacity():.2f}")
            except RuntimeError:
                parts.append("cover=dead")
        else:
            parts.append("cover=None")
        print(" ".join(parts), flush=True)

    _sampler.timeout.connect(_sample_state)
    _sampler.start()

    def _max():
        print("STAGE: max fired", flush=True)
        window.startup_fullscreen()

    def _restore():
        print("STAGE: restore fired", flush=True)
        window._on_escape()
        print("STAGE: restore returned", flush=True)

    def _quit():
        print("STAGE: quit fired", flush=True)
        st2 = QSettings("MimoWorks", "SerialAssistant")
        for k in set(st2.allKeys()) - set(_SNAPSHOT):
            st2.remove(k)
        for k, v in _SNAPSHOT.items():
            st2.setValue(k, v)
        st2.sync()
        print("STAGE: settings restored", flush=True)
        app.quit()

    # ⚠️ 此探针间歇性 exec 不返回（退出路径竞态，与被测动画无关）：
    #    看门狗 12s 后硬退兜底（QSettings 已在上面还原过）。
    import threading
    _wd = threading.Timer(12.0, lambda: os._exit(3))
    _wd.daemon = True
    _wd.start()

    QTimer.singleShot(600, _max)
    QTimer.singleShot(1800, _restore)
    QTimer.singleShot(3000, _quit)
    print("STAGE: timers scheduled", flush=True)
    app.exec()
    _wd.cancel()
    print("STAGE: exec returned", flush=True)


def record_and_analyze() -> None:
    rec = os.path.join(OUT, "restore_v2.mp4")
    if os.path.exists(rec):
        os.remove(rec)
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "gdigrab", "-framerate", "30",
         "-offset_x", "0", "-offset_y", "0",
         "-video_size", "3200x2000", "-i", "desktop", "-t", "10",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
         rec],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    rc = main()
    time.sleep(2.0)
    try:
        ff.stdin.write(b"q")
        ff.stdin.close()
    except Exception:
        pass
    try:
        ff.wait(timeout=15)
    except subprocess.TimeoutExpired:
        ff.terminate()
    print("app rc:", rc)

    cap = cv2.VideoCapture(rec)
    rects = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        ys, xs = np.where(gray > 40)
        if len(xs) == 0:
            rects.append((-1, -1, 0, 0))
        else:
            rects.append((int(xs.min()), int(ys.min()),
                          int(xs.max() - xs.min() + 1),
                          int(ys.max() - ys.min() + 1)))
    cap.release()
    n = len(rects)
    print(f"frames={n}")
    # 找「从满屏宽骤降」的帧 = 还原起点
    drop_idx = None
    for i in range(1, n):
        if (rects[i][2] > 0 and rects[i - 1][2] > 3000
                and rects[i][2] < rects[i - 1][2] - 300):
            drop_idx = i
            break
    print("drop_idx:", drop_idx)
    lo = max(0, (drop_idx or 0) - 4)
    hi = min(n, (drop_idx or n) + 30)
    for i in range(lo, hi):
        x, y, w_, h_ = rects[i]
        print(f"f{i:04d}  x={x} y={y} {w_}x{h_}")
    # 导出关键帧
    cap = cv2.VideoCapture(rec)
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if lo <= idx < hi:
            cv2.imwrite(os.path.join(OUT, f"f{idx:04d}.png"), frame)
        idx += 1
    cap.release()
    print("PNGs saved:", OUT)


if __name__ == "__main__":
    record_and_analyze()
