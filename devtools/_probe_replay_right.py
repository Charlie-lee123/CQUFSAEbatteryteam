# -*- coding: utf-8 -*-
"""临时：放大回放条右端，看内嵌按钮会不会顶出 26px 圆角。"""
import ctypes
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

from PySide6.QtCore import QPoint, QRect, QSettings
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
from ui_theme import apply_scheme, ui_font

app.setStyle("Fusion")
app.setFont(ui_font(12.5))
apply_scheme("dark")
from main_window import MainWindow


def pump(s):
    end = time.time() + s
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


settings = QSettings("MimoWorks", "SerialAssistant")
snapshot = {k: settings.value(k) for k in settings.allKeys()}
# ⚠️ 不要 remove("geometry")（同 _probe_ui_details 的说明）：关窗时窗口会把
# 自己的几何写回配置，可能落在还原之后，把用户记住的尺寸搞丢。

w = MainWindow()
w._port_watch.stop()
w.resize(1200, 820)
w.show()
pump(0.9)
w._replay_active = True
w._refresh_replay_ui()
pump(0.5)
bar = w._replay_bar
shot = w.grab()
sc = shot.width() / w.width()
at = bar.mapTo(w, QPoint(0, 0))
# 右端 220px 宽的一条，4 倍放大
rc = shot.copy(QRect(int((at.x() + bar.width() - 220) * sc), int(at.y() * sc),
                     int(220 * sc), int(bar.height() * sc)))
rc = rc.scaled(int(rc.width() * 3), int(rc.height() * 3))
rc.save(os.path.join(_ROOT, "_check", "probe_replay_right.png"))
print("截图 -> _check/probe_replay_right.png")
btn = None
for ch in bar.children():
    if ch.__class__.__name__ == "QHBoxLayout":
        for i in range(ch.count()):
            x = ch.itemAt(i).widget()
            if x is not None and x.__class__.__name__ == "InkButton":
                btn = x
print("按钮:", btn, "几何=", btn.geometry() if btn else None,
      " 条宽=", bar.width(), " 按钮右缘到条右端=",
      bar.width() - (btn.geometry().x() + btn.width()) if btn else None)

w._replay_active = False
w._refresh_replay_ui()
w.close()
pump(0.3)
for k in settings.allKeys():
    if k not in snapshot:
        settings.remove(k)
for k, v in snapshot.items():
    settings.setValue(k, v)
settings.sync()
print("设置已还原")
