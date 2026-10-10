# -*- coding: utf-8 -*-
"""出图：温度/电压越界着色、「定位异常」按钮与定位后的整行高亮。

用真机平台（windows）渲染，抓接收区与右侧功能区的特写。
"""
import os
import sys

# 脚本挪进 devtools/ 之后，运行时项目根不再自动在 sys.path 里 —— 补上
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import time

os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import QRect, QSettings
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
from ui_theme import apply_scheme

apply_scheme("dark")
from main_window import MainWindow

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_check")
settings = QSettings("MimoWorks", "SerialAssistant")
snapshot = {key: settings.value(key) for key in settings.allKeys()}
geometry = settings.value("geometry")
settings.remove("geometry")
settings.sync()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


window = MainWindow()
window._port_watch.stop()
window.resize(1180, 880)
window.show()
pump(1.6)
window.chk_timestamp.setChecked(True)      # 时间戳 = 每轮的电压段起点
window._clear_receive()

# 出图用的阈值（跑完由 QSettings 快照还原）：3700 欠压、3820/3900 超限、95 ℃ 超温
import customization
customization.set("volt_limit_low", 3750)
customization.set("volt_limit_high", 3800)
customization.set("temp_limit_low", 0)
customization.set("temp_limit_high", 60)
window._rx_highlighter.refresh()

# 照设备实际的布局（用户点名）：[时间戳] → 电压数据 → Temperature1: → 温度数据 → BMS
# 这一轮**没有 Voltage1 标题** —— 段落边界靠时间戳切，不靠标题。
# 电压：3700 欠压（下限 3750）、3820/3900 超限（上限 3800）
# 温度：22 正常、95 超上限（60）、45 正常
window._on_received("1.3700 2.3820 3.3900 4.3800\n"
                    "1.3810 2.3790 3.3820 4.3815\n"
                    "Temperature1:\n1.22 2.95 3.45 4.61\n"
                    "BMS ERROR\n5.99 6.99\n".encode("utf-8"))
window._flush_rx()
pump(0.8)

rx = window.txt_rx
origin = rx.mapTo(window, rx.rect().topLeft())
window.grab(QRect(origin.x() - 8, origin.y() - 8, rx.width() + 24, 260)).save(
    os.path.join(OUT, "alert_coloring_dark.png"))
print("saved alert_coloring_dark.png")

rail = window._rx_rail
r_origin = rail.mapTo(window, rail.rect().topLeft())
window.grab(QRect(r_origin.x() - 8, r_origin.y() - 8,
                  rail.width() + 16, rail.height() + 16)).save(
    os.path.join(OUT, "alert_rail_dark.png"))
print("saved alert_rail_dark.png")

# 点定位 → 抓高亮效果
window.btn_alert.click()
pump(0.8)
window.grab(QRect(origin.x() - 8, origin.y() - 8, rx.width() + 24, 260)).save(
    os.path.join(OUT, "alert_located_dark.png"))
print("saved alert_located_dark.png  定位到:",
      repr(window._alert_cursor.block().text() if window._alert_cursor else None))

# 浅色主题再来一张着色
window._apply_theme("light")
pump(0.8)
window.grab(QRect(origin.x() - 8, origin.y() - 8, rx.width() + 24, 260)).save(
    os.path.join(OUT, "alert_coloring_light.png"))
print("saved alert_coloring_light.png")

window.close()
if geometry is not None:
    settings.setValue("geometry", geometry)
for key in settings.allKeys():
    if key not in snapshot:
        settings.remove(key)
for key, value in snapshot.items():
    settings.setValue(key, value)
settings.sync()
