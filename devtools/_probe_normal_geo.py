# -*- coding: utf-8 -*-
r"""探针：还原/最大化闪烁根因定位 —— _normal_geometry 时间线。

用户录屏证据（devtools 逐帧分析）：
  还原动画中段一帧主窗完全透明（露出桌面），结束后窗口铺满工作区（任务栏可见），
  没有回到 1134x820 普通窗口。
两个待验证假设：
  H1. animations_enabled() 在本机返回 False → 还原走"无动画 else 分支 + 淡入"
      （淡入 = 录屏里的透明帧）。
  H2. _normal_geometry 在进入全屏前后被污染成铺满矩形 → 还原目标就是铺满
      （= 录屏里结束后铺满）。
本探针每 40ms 采样一次窗口状态，触发 startup_fullscreen → _on_escape，dump 时间线。
QSettings 全量快照/还原。QT_QPA_PLATFORM=windows。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_SNAPSHOT: dict = {}


def main() -> int:
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    app = QApplication.instance() or QApplication(sys.argv)
    from ui_anim import animations_enabled
    from main_window import MainWindow

    print(f"animations_enabled() = {animations_enabled()}   <<< H1")

    window = MainWindow()
    window._port_watch.stop()
    scr = app.primaryScreen().availableGeometry()
    w, h = 1134, 820
    window.setGeometry(scr.x() + (scr.width() - w) // 2,
                       scr.y() + max(60, (scr.height() - h) // 3), w, h)
    window.show()

    samples = []
    t0 = time.monotonic()

    def sample():
        g = window.geometry()
        ng = window._normal_geometry
        samples.append((
            round(time.monotonic() - t0, 2),
            (g.x(), g.y(), g.width(), g.height()),
            None if ng is None else (ng.x(), ng.y(), ng.width(), ng.height()),
            int(window.isFullScreen()), int(window.isMaximized()),
            round(window.windowOpacity(), 2),
            int(window._expect_normal), int(window._geo_animating),
        ))

    timer = QTimer()
    timer.timeout.connect(sample)
    timer.start(40)

    def report():
        timer.stop()
        print(f"{'t':>6} {'geometry':>28} {'normal_geometry':>28} "
              f"{'fs':>2} {'max':>3} {'op':>5} {'expN':>4} {'anim':>4}")
        for row in samples:
            print(f"{row[0]:>6} {str(row[1]):>28} {str(row[2]):>28} "
                  f"{row[3]:>2} {row[4]:>3} {row[5]:>5} {row[6]:>4} {row[7]:>4}")
        # 结论
        final = samples[-1]
        print("\n=== 结论 ===")
        print(f"  H1 animations_enabled={animations_enabled()}")
        print(f"  还原后 geometry={final[1]}  normal_geometry={final[2]}")
        want = (scr.x() + (scr.width() - w) // 2,
                scr.y() + max(60, (scr.height() - h) // 3), w, h)
        ok = all(abs(a - b) <= 2 for a, b in zip(final[1], want))
        print(f"  回到目标普通窗口({want})：{'✓' if ok else '✗ ← 复现了！'}")
        app.quit()

    def esc():
        sample()
        window._on_escape()
        QTimer.singleShot(1200, report)

    QTimer.singleShot(700, window.startup_fullscreen)
    QTimer.singleShot(2200, esc)
    QTimer.singleShot(5000, report)   # 兜底
    app.exec()

    st2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st2.allKeys()) - set(_SNAPSHOT):
        st2.remove(k)
    for k, v in _SNAPSHOT.items():
        st2.setValue(k, v)
    st2.sync()
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
