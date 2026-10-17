# -*- coding: utf-8 -*-
r"""把"接收区最大化"整个过程逐帧拍成一条，用来肉眼定位跳变发生在哪一帧。

用户反馈"感觉还是有跳变"。前面只量了发送区高度轨迹（单调 ✓），但整个最大化里
还有几处是**瞬时**的：工具栏整行 hide、串口开关 reparent 到右侧列、磨砂颗粒
在动画期间被跳过……这些都可能是"跳一下"的来源。逐帧看图最直接。

用法：python devtools\_probe_maximize_frames.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings                       # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication                 # noqa: E402

OUT = os.path.join(_ROOT, "_check")
STEP_MS = 30
FRAMES = 12


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    import customization
    from ui_theme import apply_scheme, ui_font
    from main_window import MainWindow

    for spec in customization.specs():
        customization.set(spec["key"], spec["default"])
    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    window = MainWindow()
    window._port_watch.stop()
    screen = app.primaryScreen().availableGeometry()
    window.setGeometry(screen.x() + 60, screen.y() + 50, 1180, 830)
    window.show()
    window.raise_()
    for _ in range(100):
        app.processEvents()
    time.sleep(0.4)
    for _ in range(40):
        app.processEvents()

    # ⚠️ 两条教训：① window.grab() 在窗口缩放/全屏切换期间返回**全黑**；
    #    ② grabWindow(0, x, y, w, h) 传窗口几何也不行 —— 过渡期 geometry()
    #    读出来是旧值/0，抓到的还是黑块。**固定抓整屏**（不带矩形参数）最稳，
    #    回头再裁。
    shots = []
    window._expand_receive()
    t0 = time.perf_counter()
    next_at = 0.0
    while len(shots) < FRAMES and time.perf_counter() - t0 < 1.2:
        if (time.perf_counter() - t0) * 1000 >= next_at:
            shots.append(((time.perf_counter() - t0) * 1000,
                          app.primaryScreen().grabWindow(0).toImage()))
            next_at += STEP_MS
        app.processEvents()
        time.sleep(0.001)

    time.sleep(0.4)
    for _ in range(60):
        app.processEvents()

    # 横向拼一条，每帧下面标时间与几个关键量
    from PySide6.QtCore import Qt
    h = 300
    pad, label = 20, 44
    # 整屏图裁到窗口那一块（屏幕中左部），拼起来才看得清
    scr = app.primaryScreen().size()
    crop_w = min(int(scr.width() * 0.72), shots[0][1].width())
    shots = [(ms, img.copy(0, 0, crop_w, img.height())) for ms, img in shots]
    w = shots[0][1].width() if shots else 300
    sheet = QImage(pad * 2 + (w + pad) * len(shots), h + label + pad,
                   QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#0D0D0F"))
    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setFont(QFont("Microsoft YaHei UI", 10))
    for i, (ms, img) in enumerate(shots):
        x = pad + i * (w + pad)
        painter.drawImage(x, label,
                          img.scaledToHeight(h, Qt.TransformationMode.SmoothTransformation))
        painter.setPen(QColor("#F2F3F5"))
        painter.drawText(x, 30, f"{ms:.0f}ms")
        painter.setPen(QColor("#9CA1A8"))
        painter.drawText(x, h + label + 14, f"tx={window._tx_card.height()}")
    painter.end()
    path = os.path.join(OUT, "maximize_frames.png")
    print("帧条:", sheet.save(path, "PNG"), path)
    print("帧数:", len(shots), " 每帧间隔约", STEP_MS, "ms")

    window._collapse_receive()
    for _ in range(240):
        app.processEvents()
        time.sleep(0.004)
    window.setGeometry(0, 0, 1134, 820)
    window.close()
    for key, value in snapshot.items():
        if value is not None:
            settings.setValue(key, value)
    settings.sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
