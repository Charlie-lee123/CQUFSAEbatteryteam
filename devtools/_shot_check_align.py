# -*- coding: utf-8 -*-
"""截图：设置面板底部按钮区 + 定制功能数字框区 + 串口配置栏，用于核对文字垂直位置。

只读，可在主工作区安全运行（自带 QSettings 快照还原）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QScrollArea

SCALE = 2


def enlarge(widget) -> QImage:
    pix = widget.grab()
    return pix.toImage().scaled(
        int(pix.width() * SCALE), int(pix.height() * SCALE),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation)


def stack(images, path: str, gap: int = 8) -> None:
    width = max(img.width() for img in images)
    height = sum(img.height() for img in images) + gap * (len(images) + 1)
    canvas = QImage(width, height, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#0d0d0f"))
    painter = QPainter(canvas)
    y = gap
    for img in images:
        painter.drawImage(0, y, img)
        y += img.height() + gap
    painter.end()
    canvas.save(path)
    print("saved", path)


def main() -> None:
    import app_log

    app_log.init()
    from main_window import MainWindow
    from ui_settings import SettingsDialog
    from ui_theme import apply_scheme, current_scheme

    from ui_theme import ui_font

    app = QApplication.instance() or QApplication(sys.argv)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    original = current_scheme()
    # ⚠️ 必须像 main.py 一样起手（Fusion + 主题 + 应用字体）：不设应用字体时，
    # 所有"继承默认字体"的控件会退化成系统 9pt —— 拍出来的不是用户看到的界面，
    # 之前拿它核对过一次，从头错到尾（踩过）。
    app.setStyle("Fusion")
    apply_scheme("dark")
    app.setFont(ui_font(12.5))
    dialog = None
    window = MainWindow()
    try:
        window.showNormal()
        window.show()
        for _ in range(20):
            app.processEvents()

        for scheme in ("dark", "light"):
            apply_scheme(scheme)
            for _ in range(15):
                app.processEvents()
            suffix = "" if scheme == "dark" else "_light"
            # 串口配置栏（竖着排五行）
            stack([enlarge(w) for w in (window.cmb_baud, window.cmb_stopbits,
                                        window.cmb_bytesize, window.cmb_parity,
                                        window.spin_rx_lines)],
                  os.path.join(ROOT, "_check", f"check_serial_form{suffix}.png"))
            # 设置面板：定制功能的数字框（面板一次就够，深色为主）
            if dialog is None:
                dialog = SettingsDialog("dark", 0)
                dialog.show()
                for _ in range(25):
                    app.processEvents()
                scroll = dialog.findChildren(QScrollArea)[0]
                numeric = [dialog._custom_widgets[k] for k in
                           ("pause_snap_lines", "volt_limit_low", "volt_limit_high",
                            "temp_limit_low", "temp_limit_high")]
                scroll.ensureWidgetVisible(numeric[1], 0, 120)
                for _ in range(15):
                    app.processEvents()
                stack([enlarge(w) for w in numeric],
                      os.path.join(ROOT, "_check", "check_spin_column.png"))
                # 底部：导出日志 / 检查更新
                bar = scroll.verticalScrollBar()
                bar.setValue(bar.maximum())
                for _ in range(25):
                    app.processEvents()
                bottom = [dialog.btn_export_log, dialog.btn_check_update, dialog]
                stack([enlarge(bottom[0]), enlarge(bottom[1]),
                       enlarge(dialog).scaledToWidth(enlarge(bottom[0]).width(),
                                                     Qt.TransformationMode.SmoothTransformation)],
                      os.path.join(ROOT, "_check", "check_bottom.png"))
                # 设置面板的主题下拉框（'深色'/'浅色'——非编辑下拉的实测自适应）
                stack([enlarge(dialog.findChildren(__import__(
                    "PySide6.QtWidgets", fromlist=["QComboBox"]).QComboBox)[0])],
                      os.path.join(ROOT, "_check", "check_settings_combo.png"))
    finally:
        if dialog is not None:
            dialog.close()
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
