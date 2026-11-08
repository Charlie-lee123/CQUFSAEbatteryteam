# -*- coding: utf-8 -*-
"""验证 grabWindow 矩形抓取的内容是否正确（高DPI 坐标/dpr）。"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt
app = QApplication(sys.argv)
win = QLabel()
win.setStyleSheet("background: #FF0000;")
win.setGeometry(300, 200, 1134, 820)
win.show()
scr = app.primaryScreen()

def sample(pix, name):
    img = pix.toImage()
    dpr = pix.devicePixelRatio()
    print(f"{name}: size={pix.width()}x{pix.height()} dpr={dpr} "
          f"logical={pix.width()/dpr:.0f}x{pix.height()/dpr:.0f} "
          f"pixel(4,4)={img.pixelColor(4,4).name()} "
          f"center={img.pixelColor(img.width()//2, img.height()//2).name()}")

# 矩形抓取
pix_rect = scr.grabWindow(0, win.x(), win.y(), win.width(), win.height())
sample(pix_rect, "rect grab")
# 整屏抓取后裁剪
full = scr.grabWindow(0)
dpr = full.devicePixelRatio()
crop = full.copy(int(win.x()*dpr), int(win.y()*dpr),
                 int(win.width()*dpr), int(win.height()*dpr))
sample(crop, "full+crop ")
print("window dpr:", win.devicePixelRatioF(), "screen dpr:", scr.devicePixelRatio())
