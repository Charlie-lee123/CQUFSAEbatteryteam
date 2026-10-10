"""临时探针：电源开关为什么点不动 + 时间戳打了几处。

用 QTest 走**窗口级**派发（QWindowSystemInterface），也就是真实鼠标走的那条
命中测试路径 —— 直接 QTest.mouseClick(button) 是绕过命中测试的，测不出这个问题。
"""

import os
import sys

# 脚本挪进 devtools/ 之后，运行时项目根不再自动在 sys.path 里 —— 补上
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

from main_window import MainWindow

settings = QSettings("MimoWorks", "SerialAssistant")
snapshot = {k: settings.value(k) for k in settings.allKeys()}

window = MainWindow()
window._port_watch.stop()
window.resize(1060, 920)
window.show()
app.processEvents()

button = window.btn_open
print("父控件:", type(button.parentWidget()).__name__)
print("页头透明穿透:", window.brand_header.testAttribute(
    Qt.WidgetAttribute.WA_TransparentForMouseEvents))

center = QPoint(button.width() // 2, button.height() // 2)
print("widgetAt:", QApplication.widgetAt(button.mapToGlobal(center)))
print("childAt :", window.brand_header.childAt(button.geometry().center()))

hits = []
button.clicked.connect(lambda: hits.append(1))
QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                 button.mapTo(window, center))
app.processEvents()
print("窗口级点击命中按钮:", bool(hits))

# ---- 时间戳：按真实设备的整轮数据喂一遍，看打了几处 ----
print("\n--- 时间戳 ---")
window.chk_timestamp.setChecked(True)
window._clear_receive()
frame = "".join(
    f"Voltage{i}:\n"
    + " ".join(f"{j}.3820" for j in range(1, 26)) + "\n\n"
    for i in range(1, 6)
) + "Temperature1:\n1.22 2.22 3.22 4.25\n5.27 6.25 7.22 8.22\n"
payload = frame.encode("utf-8")
print("整轮字节数:", len(payload))
for start in range(0, len(payload), 200):
    window._on_received(payload[start:start + 200])
    app.processEvents()
window._flush_rx()
app.processEvents()
lines = window.txt_rx.toPlainText().split("\n")
print("显示行数:", len(lines))
stamped = [l for l in lines if l.startswith("[")]
print("带时间戳的行数:", len(stamped))
for line in stamped[:8]:
    print("   ", line[:60])

window.close()
for key in settings.allKeys():
    if key not in snapshot:
        settings.remove(key)
for key, value in snapshot.items():
    settings.setValue(key, value)
settings.sync()
