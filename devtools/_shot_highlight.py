# -*- coding: utf-8 -*-
"""截图：异常高亮的覆盖范围（越界条目整条含序号；正常条目只有序号蓝）。

只读，可在主工作区安全运行（自带 QSettings 快照还原）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


def main() -> None:
    import customization
    from main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    window = MainWindow()
    try:
        window.resize(1180, 720)
        window.show()
        app.processEvents()
        window.chk_timestamp.setChecked(True)
        # 上限 3825 / 下限 3700（mV）：六个点里只有 3827 超上限、3690 欠压
        customization.set("volt_limit_high", 3825)
        customization.set("volt_limit_low", 3700)
        customization.set("highlight_errors", True)
        window._rx_highlighter.refresh()
        window._clear_receive()
        window._on_received(("Voltage1:\n1.3700 2.3824 3.3827 4.3810 5.3690 6.3820\n"
                             "Temperature1:\n1.22 2.95 3.45\n"
                             "BMS ERROR\n1.1 Over Voltage\n").encode("utf-8"))
        window._flush_rx()
        for _ in range(40):
            app.processEvents()
        path = os.path.join(ROOT, "_check", "highlight_scope.png")
        window.grab().save(path)
        print("saved", path)

        # 同一份数据、关掉开关的对照图
        customization.set("highlight_errors", False)
        window._rx_highlighter.refresh()
        for _ in range(40):
            app.processEvents()
        path_off = os.path.join(ROOT, "_check", "highlight_scope_off.png")
        window.grab().save(path_off)
        print("saved", path_off)
    finally:
        window.close()
        app.processEvents()
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()


if __name__ == "__main__":
    main()
