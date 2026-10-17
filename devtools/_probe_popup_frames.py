# -*- coding: utf-8 -*-
r"""把两个下拉的展开过程逐帧抓下来拼成两条，直接看"动效到底差在哪"。

左条：波特率（用户说"要照着的那个"）  右条：编码（用户说"不一样"的那个）
每 25ms 抓一帧容器窗口，横向拼成序列。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QPoint, QSettings, Qt         # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

OUT = os.path.join(_ROOT, "_check")
FRAMES = 7
STEP_MS = 26


def record(app, cmb, tag: str) -> list[QImage]:
    cont = cmb.view().window()
    anchor = cmb.mapToGlobal(QPoint(0, cmb.height() // 2)).y()
    shots: list[QImage] = []
    cmb.showPopup()
    t0 = time.perf_counter()
    next_at = 0.0
    while len(shots) < FRAMES and time.perf_counter() - t0 < 1.5:
        if (time.perf_counter() - t0) * 1000 >= next_at:
            img = cont.grab().toImage()
            shots.append(img)
            print(f"   {tag} 帧{len(shots)}: {img.width()}x{img.height()} "
                  f"几何={cont.geometry().y()},{cont.geometry().height()} "
                  f"opacity={cont.windowOpacity():.2f}")
            next_at += STEP_MS
        app.processEvents()
        time.sleep(0.002)
    g = cont.geometry()
    below = (g.y() + g.height() / 2) >= anchor
    print(f"   {tag}: 落位{'下方' if below else '上方'} 终态 y={g.y()} h={g.height()}")
    cmb.hidePopup()
    for _ in range(20):
        app.processEvents()
    return shots


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
    window.setGeometry(screen.x() + 80, screen.y() + 60, 1180, 860)
    window.show()
    window.raise_()
    for _ in range(80):
        app.processEvents()

    print("== 波特率（参照）")
    baud = record(app, window.cmb_baud, "波特率")
    print("== 编码（被说不一样）")
    enc = record(app, window.cmb_encoding, "编码")

    h = 340
    pad = 16
    row_h = h + 46
    sheet = QImage(pad * 3 + baud[0].width() * FRAMES, row_h * 2 + pad, QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#0D0D0F"))
    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setFont(QFont("Microsoft YaHei UI", 10))
    for row, (shots, title) in enumerate(((baud, "波特率（参照）"), (enc, "编码"))):
        y = pad + row * row_h
        painter.setPen(QColor("#F2F3F5"))
        painter.drawText(pad, y + 14, f"{title} —— 每 {STEP_MS}ms 一帧")
        for i, img in enumerate(shots):
            scaled = img.scaledToHeight(h, Qt.TransformationMode.SmoothTransformation)
            painter.drawImage(pad + i * baud[0].width(), y + 26, scaled)
    painter.end()
    path = os.path.join(OUT, "popup_open_frames.png")
    print("条图:", sheet.save(path, "PNG"), path)

    window.setGeometry(0, 0, 1134, 820)
    window.close()
    for key, value in snapshot.items():
        if value is not None:
            settings.setValue(key, value)
    settings.sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
