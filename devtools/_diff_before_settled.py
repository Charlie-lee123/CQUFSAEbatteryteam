import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
app = QApplication([])
R = _ROOT + "/devtools/_frames_swap_instant"
a = QImage(R + "/0_before_swap.png").convertToFormat(QImage.Format_RGB888)
b = QImage(R + "/2_settled.png").convertToFormat(QImage.Format_RGB888)
n = tot = 0
maxd = 0
for y in range(0, a.height(), 2):
    for x in range(0, a.width(), 2):
        pa, pb = a.pixelColor(x, y), b.pixelColor(x, y)
        d = abs(pa.red()-pb.red())+abs(pa.green()-pb.green())+abs(pa.blue()-pb.blue())
        maxd = max(maxd, d)
        if d > 60:
            n += 1
        tot += 1
print(f"换帧前(快照全尺寸) vs 稳态: 差异>60 = {n}/{tot} ({100*n/tot:.3f}%), max_diff={maxd}")
