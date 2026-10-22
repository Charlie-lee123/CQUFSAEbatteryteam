# -*- coding: utf-8 -*-
"""「模组个数」数字框竖直居中测量：grab 后数墨迹的上下留白。

⛔ 不写任何配置。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

import ui_settings  # noqa: E402
import ui_theme  # noqa: E402

app = QApplication(sys.argv)
# ⛔ 不调 apply_scheme 就没有全站 QSS（设置面板单独打开时不挂），量出来的
#    是裸 Fusion —— 必须先挂上。set_scheme 纯内存、不写 QSettings。
ui_theme.apply_scheme("dark")
dialog = ui_settings.SettingsDialog("dark", 55)
dialog.resize(dialog.minimumWidth(), 700)
app.processEvents()

spin = dialog.spin_vehicle_modules
spin_val = spin.value()
print("spin:", spin_val, "size:", spin.width(), "x", spin.height(),
      "font:", spin.font().pointSizeF(), "dpr:", spin.devicePixelRatioF())
print("edit:", spin._edit.width(), "x", spin._edit.height(),
      "y:", spin._edit.y(), "margins:", spin._edit.textMargins())

img = spin.grab().toImage()
dpr = img.devicePixelRatio() or 1.0
w, h = img.width(), img.height()
# 背景取框内（顶部中央、文字上方），避开 1px 描边
bg = img.pixelColor(int(w * 0.5), 5)


def is_ink(c) -> bool:
    return abs(c.red() - bg.red()) + abs(c.green() - bg.green()) \
        + abs(c.blue() - bg.blue()) > 60


# 只扫左半边（右侧是 chevron），x 跳过左侧圆角弧（半径 ~16 逻辑 = 32 物理），
# y 避开描边及其抗锯齿 —— 否则圆角弧线会被当成"墨迹"，量出来永远占满全框
top = bottom = None
for y in range(6, h - 6):
    has = any(is_ink(img.pixelColor(x, y)) for x in range(40, int(w * 0.55)))
    if has and top is None:
        top = y
    if has:
        bottom = y
print(f"图像 {w}x{h} (dpr {dpr})  文字墨迹 top={top} bottom={bottom}")
if top is not None:
    gap_top, gap_bottom = top - 3, (h - 4) - bottom
    print(f"描边内上留白 {gap_top}px  下留白 {gap_bottom}px  (逻辑: "
          f"上 {gap_top/dpr:.1f} / 下 {gap_bottom/dpr:.1f})")
    bad = abs(gap_top - gap_bottom) > 4
    print("FAIL 上下留白差 > 2px 逻辑" if bad else "OK 上下对称")
img.copy(0, 0, w, h).scaled(w * 3, h * 3).save(
    os.path.join(ROOT, "_check", "spin_modules_zoom.png"))
print("saved _check/spin_modules_zoom.png")
