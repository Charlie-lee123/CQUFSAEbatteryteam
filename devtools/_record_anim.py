# -*- coding: utf-8 -*-
r"""录屏：软件最大化 / 还原 / 最小化还原动画（2026-11-03 用户要求「录屏给我展示动效」）。

用 ffmpeg gdigrab 以 60fps 录主屏，同时用 QTimer 时间线驱动真实用户路径：
  t=0.8s  标题栏最大化键（startup_fullscreen，普通窗口 → 整窗全屏）
  t=2.3s  还原（_on_escape，全屏 → 普通窗口）
  t=3.8s  最小化（_minimize_animated，原生 SW_MINIMIZE）
  t=4.7s  从最小化还原（SW_RESTORE）
  t=6.4s  退出，ffmpeg 已按 -t 7.0 收尾

产物：_check/anim_record/restore_max_anim.mp4
⚠️ 用户嘱咐：这段录屏**下一次对话时要删除**（已记入项目日志）。
QSettings 全量快照/还原，不污染真实配置。QT_QPA_PLATFORM=windows。
"""
import os
import subprocess
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT_DIR = os.path.join(_ROOT, "_check", "anim_record")
OUT_MP4 = os.path.join(OUT_DIR, "restore_max_anim.mp4")
RECORD_SECONDS = 7.0

_SNAPSHOT: dict = {}


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    app = QApplication.instance() or QApplication(sys.argv)
    from main_window import MainWindow

    window = MainWindow()
    window._port_watch.stop()
    scr = app.primaryScreen().availableGeometry()
    # 普通窗口、屏幕居中（不贴边，避免被误判分屏接管）
    w, h = 1134, 820
    window.setGeometry(scr.x() + (scr.width() - w) // 2,
                       scr.y() + max(60, (scr.height() - h) // 3), w, h)
    window.show()
    window.raise_()

    import ctypes
    hwnd = int(window.winId())
    SW_MINIMIZE, SW_RESTORE = 6, 9

    # 起录屏（后台进程，-t 到点自动结束）
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "gdigrab", "-framerate", "60",
         "-i", "desktop", "-t", str(RECORD_SECONDS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", OUT_MP4],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    marks = []
    t0 = time.monotonic()

    def at(ms: int, fn):
        def fire():
            marks.append((round(time.monotonic() - t0, 2), fn.__name__))
            fn()
        QTimer.singleShot(ms, fire)

    at(800, lambda: window.startup_fullscreen())      # 软件最大化
    at(2300, window._on_escape)                        # 还原 → 普通窗口
    at(3800, window._minimize_animated)                # 最小化
    at(4700, lambda: ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE))  # 从最小化还原
    at(6400, app.quit)

    app.exec()
    ff.wait(timeout=20)
    rc = ff.returncode

    for t, name in marks:
        print(f"  t={t:>5}s  {name}")
    size = os.path.getsize(OUT_MP4) if os.path.exists(OUT_MP4) else -1
    print(f"RECORD rc={rc} size={size} -> {OUT_MP4}")

    # 还原 QSettings（窗口 close 写过 geometry 等）
    st2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st2.allKeys()) - set(_SNAPSHOT):
        st2.remove(k)
    for k, v in _SNAPSHOT.items():
        st2.setValue(k, v)
    st2.sync()
    window.close()
    return 0 if (rc == 0 and size > 100_000) else 1


if __name__ == "__main__":
    sys.exit(main())
