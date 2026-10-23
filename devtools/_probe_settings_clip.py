"""量设置面板：内容会不会被面板右边界裁掉（模块行的「温度采集数量」输入框）。

判据：任一子控件 mapTo(dialog) 后的右边界 > dialog.width() 即"被裁"。
用法：python devtools/_probe_settings_clip.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QPoint, QSettings  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from ui_settings import SettingsDialog  # noqa: E402
from ui_theme import ui_font  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    try:
        dialog = SettingsDialog("dark", 60, parent=None)
        dialog.show()
        app.processEvents()
        QTest.qWait(600)               # 等展开动画落定
        app.processEvents()
        w, h = dialog.width(), dialog.height()
        print(f"dialog {w}x{h}  minimumSizeHint={dialog.minimumSizeHint().width()}")
        worst = []
        for child in dialog.findChildren(QWidget):
            if not child.isVisible():
                continue
            right = child.mapTo(dialog, QPoint(child.width() - 1, 0)).x() + 1
            if right > w:
                name = child.objectName() or type(child).__name__
                text = ""
                if hasattr(child, "text"):
                    try:
                        text = str(child.text())[:12]
                    except TypeError:
                        text = ""
                worst.append((right, name, text, child.width()))
        worst.sort(reverse=True)
        print(f"越界控件 {len(worst)} 个（右边界 > {w}）:")
        for right, name, text, cw in worst[:12]:
            print(f"  右={right:4d} w={cw:4d}  {name} {text!r}")
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    finally:
        settings = QSettings("MimoWorks", "SerialAssistant")
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
