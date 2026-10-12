# -*- coding: utf-8 -*-
"""截图：串口配置栏五行控件的文字左边缘对齐情况（放大 3 倍）。

只读，可在主工作区安全运行（自带 QSettings 快照还原）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

SCALE = 3


def main() -> None:
    import app_log

    app_log.init()
    from main_window import MainWindow
    from ui_theme import apply_scheme, current_scheme

    app = QApplication.instance() or QApplication(sys.argv)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    original = current_scheme()
    window = MainWindow()
    try:
        apply_scheme("dark")
        window.resize(1100, 820)
        window.showNormal()
        window.show()
        for _ in range(25):
            app.processEvents()

        widgets = [window.cmb_baud, window.cmb_stopbits, window.cmb_bytesize,
                   window.cmb_parity, window.spin_rx_lines]
        images = []
        for widget in widgets:
            pix = widget.grab()
            images.append(pix.toImage().scaled(
                pix.width() * SCALE, pix.height() * SCALE,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

        gap = 10
        width = max(img.width() for img in images)
        height = sum(img.height() for img in images) + gap * (len(images) + 1)
        canvas = QImage(width, height, QImage.Format.Format_ARGB32)
        canvas.fill(QColor("#0d0d0f"))
        painter = QPainter(canvas)
        # 一条竖参考线：所有控件的文字左边缘都该贴在这条线上
        painter.setPen(QColor("#ff5555"))
        y = gap
        for img in images:
            painter.drawImage(0, y, img)
            y += img.height() + gap
        painter.end()

        path = os.path.join(ROOT, "_check", "rx_form_align.png")
        canvas.save(path)
        print("saved", path)
    finally:
        window.close()
        app.processEvents()
        apply_scheme(original)
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()


if __name__ == "__main__":
    main()
