"""实测：开屏落定帧 vs 主窗页头 logo，量墨迹包围盒偏差。

只读探针：建一次 MainWindow（全屏，不写用户配置，结束时还原 QSettings 快照）。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
st = QSettings("MimoWorks", "SerialAssistant")
SNAP = {k: st.value(k) for k in st.allKeys()}

import main_window
import splash_motion as sm

w = main_window.MainWindow()
w.apply_startup_state()
w.show()
for _ in range(8):
    app.processEvents()

W, H = w.width(), w.height()
print(f"window = {W}x{H}  fullscreen={w.isFullScreen()}")

bh = w.brand_header
r = bh.lockup_rect()
tl = bh.mapTo(w, r.topLeft().toPoint())
print(f"lockup_rect(viewBox) = x={tl.x()} y={tl.y()} "
      f"w={r.width():.2f} h={r.height():.2f}")

# ---- 主窗真实截图里的墨迹（只看左上角那块，避开标题栏和卡片）
scr = app.primaryScreen().grabWindow(0)
img = scr.toImage().convertToFormat(QImage.Format.Format_RGB32)
npx = img.bytesPerLine() // 4
arr = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(img.height(), npx, 4)
g = arr[:, :img.width(), :3].mean(axis=2)
y0, y1, x0, x1 = 100, 420, 40, 1400
sub = g[y0:y1, x0:x1]
ys, xs = np.where(sub > 60)
ui = (int(xs.min()) + x0, int(ys.min()) + y0,
      int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
print(f"UI   墨迹 bbox = {ui}")

# ---- 动画落定帧（注入与 main 相同的真实落点）
mx = tl.x() + r.width() * sm._INK_LEFT
my = tl.y() + r.height() * sm._INK_TOP
mh = r.height() * sm._INK_H_FRAC
print(f"注入墨迹几何  = x={mx:.2f} y={my:.2f} h={mh:.2f}")

ren = sm.MotionRenderer("dark", 1.0)
ren.set_land(mx / W, my / H, mh / H)
canvas = QImage(W, H, QImage.Format.Format_RGB32)
canvas.fill(0)
p = QPainter(canvas)
ren.paint(p, W, H, sm.T_TOTAL)
p.end()
ci = canvas.convertToFormat(QImage.Format.Format_Grayscale8)
ca = np.frombuffer(ci.constBits(), dtype=np.uint8).reshape(
    H, ci.bytesPerLine())[:, :W]
ys2, xs2 = np.where(ca > 60)
sp = (int(xs2.min()), int(ys2.min()),
      int(xs2.max() - xs2.min() + 1), int(ys2.max() - ys2.min() + 1))
print(f"动画 墨迹 bbox = {sp}")
print(f"偏差 dx={sp[0]-ui[0]:+d} dy={sp[1]-ui[1]:+d} "
      f"dw={sp[2]-ui[2]:+d} dh={sp[3]-ui[3]:+d}")

# ---- 还原配置
for k in list(st.allKeys()):
    if k not in SNAP:
        st.remove(k)
for k, v in SNAP.items():
    st.setValue(k, v)
st.sync()
