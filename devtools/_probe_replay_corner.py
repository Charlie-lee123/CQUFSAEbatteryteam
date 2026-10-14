# -*- coding: utf-8 -*-
"""在真实主窗口上取样：回放条四个角到底是"透出卡片底色"还是"被自己占满"。"""
import ctypes
import os
import re
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

from PySide6.QtCore import QPoint, QSettings
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

w = MainWindow()
w._port_watch.stop()
w.resize(1200, 820)
w.show()
pump(0.9)
w._replay_active = True
w._refresh_replay_ui()
pump(0.5)

bar = w._replay_bar
pos = bar.mapTo(w, QPoint(0, 0))
shot = w.grab().toImage()
sc = shot.width() / w.width()
def px(dx, dy):
    return shot.pixelColor(int((pos.x() + dx) * sc), int((pos.y() + dy) * sc)).name()
print(f"回放条 rect=({pos.x()},{pos.y()}) {bar.width()}x{bar.height()}  "
      f"抓图缩放={sc}")
print("  条内 (10,10)  =", px(10, 10), " ← 条的底色 surface_2")
print("  条外 (-4,-4)  =", px(-4, -4), " ← 卡片/页面底色")
print("  左上角 (1,1)  =", px(1, 1), " ← 有圆角应等于'条外'色")
print("  左上角 (3,3)  =", px(3, 3))
print("  左上角 (6,6)  =", px(6, 6))
print("  左上角 (12,12)=", px(12, 12))
print("  左边缘 (1,25) =", px(1, 25), " ← 中线处的 1px 边框")
print("  顶边缘 (25,1) =", px(25, 1), " ← 中线处的 1px 边框")
qss = app.styleSheet()
m = re.search(r"#replayBar\s*\{[^}]*\}", qss)
print("应用级 QSS 里的规则:", " ".join(m.group(0).split()) if m else "(找不到!)")

w._replay_active = False
w._refresh_replay_ui()
w.close()
pump(0.3)
# 先让窗口析构（它析构时会把几何写回配置），再无条件还原快照 —— 否则那次写入
# 可能落在还原之后，把用户记住的窗口尺寸覆盖掉。
w.deleteLater()
app.processEvents()
import gc as _gc

_gc.collect()
app.processEvents()
for k in settings.allKeys():
    if k not in snapshot:
        settings.remove(k)
for k, v in snapshot.items():
    settings.setValue(k, v)
settings.sync()
print("设置已还原")
