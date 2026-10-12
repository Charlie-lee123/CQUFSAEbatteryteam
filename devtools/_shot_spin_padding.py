# -*- coding: utf-8 -*-
"""截图：「保留行数」输入框内文字上下留白的修复前后对比（放大 6 倍）。

只读，可在主工作区安全运行（自带 QSettings 快照还原）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

SCALE = 6
BG = "#f4f5f7"
INK = "#111214"


def enlarged(widget) -> QImage:
    pix = widget.grab()
    return pix.toImage().scaled(
        int(pix.width() * SCALE), int(pix.height() * SCALE),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation)


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
        apply_scheme("light")
        window.showNormal()
        window.show()
        for _ in range(20):
            app.processEvents()
        spin = window.spin_rx_lines

        # 修复后（当前的补偿生效）
        spin._place_edit()
        app.processEvents()
        after = enlarged(spin)

        # 修复前（把补偿按 0 处理，等价于没有这个修复）
        spin._text_top_compensation = lambda: 0
        spin._place_edit()
        app.processEvents()
        before = enlarged(spin)

        gap = 26
        label_h = 22
        height = label_h * 2 + before.height() + after.height() + gap * 2
        canvas = QImage(before.width(), height, QImage.Format.Format_ARGB32)
        canvas.fill(QColor(BG))
        painter = QPainter(canvas)
        font = QFont()
        font.setPointSizeF(10.5)
        painter.setFont(font)
        painter.setPen(QColor(INK))
        y = 0
        painter.drawText(8, y + 16, "修复前：上留白 7.5 / 下留白 12.5（明显偏上）")
        y += label_h
        painter.drawImage(0, y, before)
        y += before.height() + gap
        painter.drawText(8, y + 16, "修复后：上留白 9.5 / 下留白 10.5（上下相等）")
        y += label_h
        painter.drawImage(0, y, after)
        painter.end()

        path = os.path.join(ROOT, "_check", "spin_padding_compare.png")
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
