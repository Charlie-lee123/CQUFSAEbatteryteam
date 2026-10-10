# -*- coding: utf-8 -*-
"""渲染检查：毛玻璃强度 + 定时发送开关观感。"""
import os
import sys

# 脚本挪进 devtools/ 之后，运行时项目根不再自动在 sys.path 里 —— 补上
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from main_window import MainWindow

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_check")


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    app = QApplication(sys.argv)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    try:
        window = MainWindow()
        window.setGeometry(-4000, -4000, 1180, 920)
        window.show()
        deadline = time.time() + 1.2
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)

        for scheme in ("light", "dark"):
            window._apply_theme(scheme)
            app.processEvents()
            window.grab().save(os.path.join(OUT, f"layout_{scheme}.png"))
        window.close()
    finally:
        for key in list(settings.allKeys()):
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()
    print("shots ->", OUT)


if __name__ == "__main__":
    main()
