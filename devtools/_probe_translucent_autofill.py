"""真实窗口实验：WA_TranslucentBackground + setAutoFillBackground 是否吃掉透明 fillRect。"""
import os, sys, time
os.environ["QT_QPA_PLATFORM"] = "windows"
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QPainter, QPalette, QImage

class W(QWidget):
    def __init__(self, auto_fill, translucent=True):
        super().__init__(None)
        if translucent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#ff0000"))  # 不透明红
        self.setPalette(pal)
        self.setAutoFillBackground(auto_fill)
        self.resize(200, 200)
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))   # 完全透明
        p.end()

app = QApplication(sys.argv)
print("期望：透明 fillRect 生效 → alpha=0；若 autoFillBackground 抢先填了调色板 → alpha=255 且红")
for translucent in (True, False):
    for af in (True, False):
        w = W(af, translucent)
        w.show(); app.processEvents(); app.processEvents()
        img = w.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
        c = img.pixelColor(100, 100)
        print(f"  translucent={translucent!s:5} autoFill={af!s:5} → "
              f"像素 alpha={c.alpha():3d} R={c.red():3d} G={c.green():3d} B={c.blue():3d}")
        w.close(); app.processEvents()

# 渲染耗时对比（同样 200x200 放大到 1600x1000 量级，模拟全屏）
print("\n渲染耗时对比（1600x1000 窗口，每帧 fillRect 透明 + 一次 drawPixmap，60 帧）")
for translucent in (True, False):
    w = W(False, translucent); w.resize(1600, 1000); w.show(); app.processEvents()
    t0 = time.perf_counter()
    for _ in range(60):
        w.update(); app.processEvents()
    dt = (time.perf_counter() - t0) / 60 * 1000
    print(f"  translucent={translucent!s:5} → {dt:.2f} ms/帧 (含事件循环)")
    w.close(); app.processEvents()
