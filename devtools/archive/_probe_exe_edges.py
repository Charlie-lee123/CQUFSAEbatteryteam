"""临时探针：启动打包产物（启动即全屏），整屏抓图，逐像素看四边是否有缝。"""

import os
import subprocess
import sys

# 脚本挪进 devtools/ 之后，运行时项目根不再自动在 sys.path 里 —— 补上
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_check")
EXE = os.path.join(ROOT, "发布", "SerialAssistant.exe")
os.makedirs(OUT, exist_ok=True)

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

proc = subprocess.Popen([EXE])
time.sleep(16)

screen = QGuiApplication.primaryScreen()
pix = screen.grabWindow(0)
path = os.path.join(OUT, "exe_fullscreen_edges.png")
pix.save(path)
print("saved", path, pix.width(), "x", pix.height())

img = pix.toImage()
w, h = img.width(), img.height()

print("四角/边缘取样：")
for name, (x, y) in {
    "左上(0,0)": (0, 0), "上中": (w // 2, 0), "右上": (w - 1, 0),
    "左中": (0, h // 2), "右中": (w - 1, h // 2),
    "左下": (0, h - 1), "下中": (w // 2, h - 1), "右下": (w - 1, h - 1),
    "内 3px 上": (w // 2, 3), "内 3px 左": (3, h // 2),
    "内 12px 上": (w // 2, 12), "内 24px 上": (w // 2, 24),
    "内 6px 左": (6, h // 2), "内 20px 左": (20, h // 2),
}.items():
    c = img.pixelColor(x, y)
    print(f"  {name:>12} ({x},{y}) = {c.red():3d},{c.green():3d},{c.blue():3d}")

# 逐行/逐列扫，找出第一条"非边线色"的像素，量出四周留白
def row_is_border(y, ref):
    return all(abs(img.pixelColor(x, y).red() - ref.red()) < 6
               and abs(img.pixelColor(x, y).blue() - ref.blue()) < 6
               for x in range(0, w, max(1, w // 40)))


ref = img.pixelColor(w // 2, 0)
top = 0
for y in range(0, min(80, h)):
    if not row_is_border(y, ref):
        top = y
        break
print(f"顶部连续同色高度 {top}px（参考色 {ref.red()},{ref.green()},{ref.blue()}）")

subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"], capture_output=True)
print("exe 已退出")
