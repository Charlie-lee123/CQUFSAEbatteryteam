# -*- coding: utf-8 -*-
"""一次性诊断：复现探针热身序列，打印每步之后的分割器/发送区状态。"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


def pump(app, seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.004)


def state(window, tag):
    print(f"[{tag}] _tx_collapsed={window._tx_collapsed} "
          f"split={window._splitter.sizes()} "
          f"tx_card.h={window._tx_card.height()} "
          f"tx_card.min/max={window._tx_card.minimumHeight()}/{window._tx_card.maximumHeight()} "
          f"saved={window._tx_saved_sizes} "
          f"tx_content.visible={window._tx_content.isVisible()}")


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
    window.show()
    window.raise_()
    window.apply_startup_state()
    pump(app, 1.5)
    state(window, "启动落定")

    try:
        window._expand_receive()
        pump(app, 1.2)
        state(window, "热身-展开接收区后")
        window._collapse_receive()
        pump(app, 1.2)
        state(window, "热身-退出最大化后")
        window._set_send_collapsed(False, remember=False)
        pump(app, 0.8)
        state(window, "手动展开发送区后")
        window._set_send_collapsed(True)
        pump(app, 0.8)
        state(window, "手动折叠发送区后")
        # P1 触发
        window._set_send_collapsed(False)
        pump(app, 1.0)
        state(window, "P1 触发展开后")
    finally:
        window.setGeometry(0, 0, 1134, 820)
        window.close()
        for key in list(settings.allKeys()):
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            if value is not None:
                settings.setValue(key, value)
        settings.sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
