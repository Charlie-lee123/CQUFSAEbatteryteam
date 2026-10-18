"""对比换帧帧与稳态帧（同为全分辨率、同一抓取矩形），定位差异区域。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
app = QApplication([])

def load(p):
    img = QImage(p).convertToFormat(QImage.Format_RGB888)
    print(f"{os.path.basename(p)}: {img.width()}x{img.height()}")
    return img

a = load(os.path.join(_ROOT, "devtools", "_frames_open_sync_run1", "f007_swap.png"))
b = load(os.path.join(_ROOT, "devtools", "_frames_swap_instant", "2_settled.png"))
if a.size() != b.size():
    print("尺寸不同，无法直接比")
    sys.exit(1)
w, h = a.width(), a.height()
minx, miny, maxx, maxy, n, tot = w, h, -1, -1, 0, 0
col_hits = {}
for y in range(0, h, 2):
    for x in range(0, w, 2):
        pa, pb = a.pixelColor(x, y), b.pixelColor(x, y)
        d = abs(pa.red()-pb.red())+abs(pa.green()-pb.green())+abs(pa.blue()-pb.blue())
        if d > 60:
            n += 1
            minx, miny = min(minx, x), min(miny, y)
            maxx, maxy = max(maxx, x), max(maxy, y)
            col_hits[x] = col_hits.get(x, 0) + 1
        tot += 1
print(f"差异>60 的采样点: {n}/{tot} ({100*n/tot:.2f}%)")
if n:
    print(f"差异包围盒: x[{minx},{maxx}] y[{miny},{maxy}] (图 {w}x{h})")
    # 列直方 top10
    top = sorted(col_hits.items(), key=lambda kv: -kv[1])[:10]
    print("差异最集中的列(x,计数):", top)
