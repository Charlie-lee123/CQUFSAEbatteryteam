# -*- coding: utf-8 -*-
"""截图：设置面板 v1.5 新增项（接收区字号滑杆 + 预览、导出日志按钮）。

⚠️ 必须先 apply_scheme()：面板的底色、文字色、控件样式全来自全局样式表，
不应用的话截出来是 Qt 默认的浅色皮，而预览文字取的是**主题 token**（ink），
两者对不上 —— 看起来就像"预览字没画出来"（踩过）。

只读，可在主工作区安全运行（自带 QSettings 快照还原）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QScrollArea


def main() -> None:
    import app_log

    app_log.init()                       # 日志节需要 path()
    from ui_settings import SettingsDialog
    from ui_theme import apply_scheme, current_scheme

    app = QApplication.instance() or QApplication(sys.argv)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    original = current_scheme()
    try:
        for scheme in ("light", "dark"):
            apply_scheme(scheme)
            dialog = SettingsDialog(scheme, 0)
            try:
                dialog.show()
                for _ in range(20):
                    app.processEvents()
                scroll = dialog.findChildren(QScrollArea)[0]
                print(f"[{scheme}] dialog={dialog.width()}x{dialog.height()} "
                      f"viewport={scroll.viewport().width()} body={scroll.widget().width()}")
                slider = dialog._custom_widgets["rx_font_size"]
                scroll.ensureWidgetVisible(slider, 0, 150)
                for _ in range(30):
                    app.processEvents()
                path_font = os.path.join(ROOT, "_check", f"settings_v15_font_{scheme}.png")
                dialog.grab().save(path_font)
                print("  saved", path_font)

                bar = scroll.verticalScrollBar()
                bar.setValue(bar.maximum())
                for _ in range(30):
                    app.processEvents()
                path_log = os.path.join(ROOT, "_check", f"settings_v15_log_{scheme}.png")
                dialog.grab().save(path_log)
                print("  saved", path_log)
            finally:
                dialog.close()
                app.processEvents()
    finally:
        apply_scheme(original)
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()


if __name__ == "__main__":
    main()
