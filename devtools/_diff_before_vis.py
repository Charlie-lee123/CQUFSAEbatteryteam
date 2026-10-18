import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication
app = QApplication([])
R = _ROOT + "/devtools/_frames_swap_instant"
a = QImage(R + "/0_before_swap.png").convertToFormat(QImage.Format_RGB888)
b = QImage(R + "/2_settled.png").convertToFormat(QImage.Format_RGB888)
out = QImage(b.width(), b.height(), QImage.Format_RGB888)
out.fill(QColor(30, 30, 30))
for y in range(b.height()):
    for x in range(b.width()):
        pa, pb = a.pixelColor(x, y), b.pixelColor(x, y)
        d = abs(pa.red()-pb.red())+abs(pa.green()-pb.green())+abs(pa.blue()-pb.blue())
        if d > 60:
            out.setPixelColor(x, y, QColor(255, 40, 40))
        else:
            g = (pb.red()+pb.green()+pb.blue())//4
            out.setPixelColor(x, y, QColor(g, g, g))
out.scaledToWidth(520).save(_ROOT + "/devtools/_diff_before_vis.png")
print("saved")
