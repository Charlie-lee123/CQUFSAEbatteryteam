# -*- coding: utf-8 -*-
"""临时：确认标题栏出血后，卡片/页头的位置与出血前一致（左右各 12px 白边）。"""
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

from PySide6.QtCore import QPoint, QSettings
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
from ui_theme import GAP, SHADOW_PAD, apply_scheme, ui_font

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
# ⚠️ 不要 remove("geometry")：窗口关窗会写回自己的几何，那次写可能落在本探针
# 的还原之后（QSettings 析构才 flush），会把用户记住的窗口尺寸搞丢。留着不动、
# 最后按快照整体还原最稳（本探针自己会 resize 到固定尺寸，不依赖记住的值）。

w = MainWindow()
w._port_watch.stop()
w.resize(1200, 820)
w.show()
pump(0.9)
print(f"窗口 {w.width()}x{w.height()}  期望白边 GAP-SHADOW_PAD = {GAP - SHADOW_PAD}")
for name, widget in (("品牌页头", w.brand_header),
                     ("接收区卡", w._rx_card),
                     ("串口配置卡", w._config_card)):
    g = widget.geometry()
    # ⚠️ mapTo 收的是**控件自己坐标系**里的点，别传 geometry().topLeft()
    #    （那是父坐标系里的，会把它在父里的偏移重复算一遍）
    p = widget.mapTo(w, QPoint(0, 0))
    print(f"  {name}: 窗口内左上=({p.x()},{p.y()}) 尺寸={g.width()}x{g.height()}"
          f"  左缘={p.x()}px 右边缘离窗口右边={w.width() - (p.x() + g.width())}px")
print("  底栏计数标签右端:",
      w.width() - (w.lbl_count.mapTo(w, QPoint(0, 0)).x() + w.lbl_count.width()))
shot = w.grab()
shot.save(os.path.join(_ROOT, "_check", "after_bleed_full.png"))
# 顶部整条（看标题栏与页头的衔接）
top = shot.copy(0, 0, shot.width(), int(120 * shot.width() / w.width()))
top.save(os.path.join(_ROOT, "_check", "after_bleed_top.png"))
print("截图 -> _check/after_bleed_full.png / after_bleed_top.png")

w.close()
pump(0.3)
for k in settings.allKeys():
    if k not in snapshot:
        settings.remove(k)
for k, v in snapshot.items():
    settings.setValue(k, v)
settings.sync()
print("设置已还原")
