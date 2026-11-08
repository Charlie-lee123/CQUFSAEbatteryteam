# -*- coding: utf-8 -*-
r"""录屏诊断①：中间键最大化/还原「闪」（2026-11-04 用户报，附截图）。

源码级驱动**真实切换路径**（startup_fullscreen / _on_escape，与点中间键完全同链路），
ffmpeg gdigrab 60fps 录主屏。时间线：先两次慢速单切（看完整动画），
再 220ms 间隔快速连点（制造补间在途折返），最后一次还原留足时间看全程。
产物 _check/anim_record/toggle_flash.mp4。QSettings 全量快照/还原。
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
OUT_MP4 = os.path.join(OUT_DIR, "toggle_flash.mp4")
RECORD_SECONDS = 7.2

_SNAPSHOT: dict = {}


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    app = QApplication.instance() or QApplication(sys.argv)
    import ui_anim
    from main_window import MainWindow

    # ⚠️ 对齐真机（main.py）：anim_fps 未改过 → display_max_hz()
    ui_anim.set_frame_hz(ui_anim.display_max_hz())

    window = MainWindow()
    window._port_watch.stop()
    scr = app.primaryScreen().availableGeometry()
    w, h = 1134, 820
    window.setGeometry(scr.x() + (scr.width() - w) // 2,
                       scr.y() + max(60, (scr.height() - h) // 3), w, h)
    window.show()
    window.raise_()

    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "gdigrab", "-framerate", "60",
         "-i", "desktop", "-t", str(RECORD_SECONDS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", OUT_MP4],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    marks = []
    t0 = time.monotonic()

    def at(ms: int, name: str, fn):
        def fire():
            marks.append((round(time.monotonic() - t0, 3), name))
            fn()
        QTimer.singleShot(ms, fire)

    # ---- 慢速两切（完整动画逐帧看）
    at(2000, "max#1", window.startup_fullscreen)
    at(3300, "restore#1", window._on_escape)
    # ---- 快速连点 220ms（补间在途折返）
    at(4200, "max#2", window.startup_fullscreen)
    at(4420, "restore#2", window._on_escape)
    at(4640, "max#3", window.startup_fullscreen)
    at(4860, "restore#3", window._on_escape)
    at(5080, "max#4", window.startup_fullscreen)
    # ---- 最后一次还原，留 1.2s 看完整还原动画
    at(5900, "restore#4", window._on_escape)
    def _quit():
        marks.append((round(time.monotonic() - t0, 3), "quit"))
        app.quit()

    at(6900, "quit", _quit)

    app.exec()
    ff.wait(timeout=25)
    rc = ff.returncode

    for t, name in marks:
        print(f"  t={t:>6.3f}s  {name}")
    size = os.path.getsize(OUT_MP4) if os.path.exists(OUT_MP4) else -1
    print(f"RECORD rc={rc} size={size} -> {OUT_MP4}")

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
