"""临时探针：量设置面板里各控件的左边缘对齐情况，以及数字框/下拉框内文字的上下留白。

真机平台跑（offscreen 的字体度量是退化值，量不出观感）。
"""

import os
import sys

# 脚本挪进 devtools/ 之后，运行时项目根不再自动在 sys.path 里 —— 补上
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QLabel, QWidget

app = QApplication(sys.argv)

from main_window import BUTTON_SPECS
from ui_settings import SettingsDialog
from ui_theme import apply_scheme

# ⚠️ 必须先铺全局样式表：QComboBox/QSpinBox 的高度、内边距全在 QSS 里，
# 不铺的话量到的是原生样式的尺寸，跟真机对不上。
apply_scheme("dark")

settings = QSettings("MimoWorks", "SerialAssistant")
snapshot = {k: settings.value(k) for k in settings.allKeys()}

dialog = SettingsDialog("dark", 0, BUTTON_SPECS, [])
dialog.show()
for _ in range(30):
    app.processEvents()

print("对话框尺寸:", dialog.size().toTuple(), "dpr:", dialog.devicePixelRatioF())


def left_of(widget: QWidget) -> int:
    return widget.mapTo(dialog, QPoint(0, 0)).x()


rows = [("主题下拉框", dialog.cmb_theme),
        ("毛玻璃滑杆", dialog.slider_glass),
        ("百分比标签", dialog.lbl_glass)]
for key, widget in dialog._custom_widgets.items():
    rows.append((f"定制/{key}", widget))
for name, widget in rows:
    top = widget.mapTo(dialog, QPoint(0, 0)).y()
    print(f"  {name:>22s}  x={left_of(widget):4d}  y={top:4d}  "
          f"w={widget.width():4d} h={widget.height():3d}")


def ink_gaps(widget: QWidget, text_zone: float = 0.45) -> tuple:
    """把控件单独渲染成图，量**文字墨迹**到上下边框的留白（逻辑像素）。

    ``text_zone`` 只扫左侧这一比例宽度 —— 右侧那 22~24px 是自绘的 chevron，
    它们的墨迹几乎占满整个内高，混进来量到的就不是文字了（第一版就踩了）。
    """
    dpr = widget.devicePixelRatioF()
    image = QImage(int(widget.width() * dpr), int(widget.height() * dpr),
                   QImage.Format.Format_ARGB32)
    image.setDevicePixelRatio(dpr)
    image.fill(0)
    widget.render(image)
    border = max(1, int(round(1 * dpr)))
    x_end = max(border + 3, int(image.width() * text_zone))

    def luma(c):
        return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()

    rows_with_ink = []
    for y in range(border + 1, image.height() - border - 1):
        values = [luma(image.pixelColor(x, y))
                  for x in range(border + 2, x_end)]
        if max(values) - min(values) > 60:
            rows_with_ink.append(y)
    if not rows_with_ink:
        return None
    return ((rows_with_ink[0] - border) / dpr,
            (image.height() - border - 1 - rows_with_ink[-1]) / dpr)


print("\n行内编辑框几何（QSpinBox 内部藏着一个 QLineEdit）：")
from PySide6.QtWidgets import QLineEdit as _QLineEdit
for key, widget in dialog._custom_widgets.items():
    edit = widget.findChild(_QLineEdit)
    if edit is None:
        continue
    print(f"  {key:>20s} spin h={widget.height():3d} edit y={edit.y():3d} "
          f"h={edit.height():3d} textMargins={edit.textMargins().top()}/"
          f"{edit.textMargins().bottom()}")
print("\n输入框内文字墨迹到上下边框的留白：")
for name, widget in [("主题下拉框", dialog.cmb_theme)] + \
        [(k, w) for k, w in dialog._custom_widgets.items()
         if w.__class__.__name__ == "ChevronSpinBox"]:
    gaps = ink_gaps(widget)
    if gaps is None:
        print(f"  {name:>22s}  没量到文字墨迹")
    else:
        print(f"  {name:>22s}  上 {gaps[0]:5.1f}  下 {gaps[1]:5.1f}  "
              f"差 {gaps[0] - gaps[1]:+5.1f}")

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_check")
dialog.grab().save(os.path.join(OUT, "settings_probe.png"))
print("\n截图:", os.path.join(OUT, "settings_probe.png"))

dialog.close()
for key in settings.allKeys():
    if key not in snapshot:
        settings.remove(key)
for key, value in snapshot.items():
    settings.setValue(key, value)
settings.sync()
