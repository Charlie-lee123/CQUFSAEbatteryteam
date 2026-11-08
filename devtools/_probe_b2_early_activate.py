"""B2 修法可行性：topmost 开屏仍在时，提前激活主窗能否让 close 撤场后主窗直接是前台。"""

import os, sys, time, ctypes

os.environ["QT_QPA_PLATFORM"] = "windows"
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter

u32 = ctypes.windll.user32
app = QApplication(sys.argv)


class W(QWidget):
    def __init__(self, color, topmost=False):
        super().__init__(None)
        f = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if topmost:
            f |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(f)
        self._c = QColor(color)
        self.resize(400, 300)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), self._c)
        p.end()


def fg():
    return int(u32.GetForegroundWindow())


def pump(ms=250):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)


def run(early: bool, label: str):
    splash = W("#0d0d0f", topmost=True)
    main = W("#1e1e22", topmost=False)
    splash.move(120, 120)
    main.move(120, 120)
    splash.show()
    main.show()
    pump()
    splash.raise_()
    splash.activateWindow()
    pump()
    print(f"\n[{label}] 起手 fg==开屏: {fg() == int(splash.winId())}")
    if early:
        main.raise_()
        main.activateWindow()
        pump()
        print(
            f"  淡出前提前激活后 fg==主窗: {fg() == int(main.winId())}"
            f"  开屏仍可见: {splash.isVisible()}"
        )
    else:
        print("  （不提前激活，模拟现状）")
    splash.close()
    pump(350)
    ok = fg() == int(main.winId())
    print(
        f"  开屏 close 后 fg==主窗: {ok}   fg=0x{fg():X} main=0x{int(main.winId()):X}"
    )
    main.close()
    pump(50)
    return ok


r1 = run(False, "对照：现状（activate 在 close 之后）")
r2 = run(True, "方案①：淡出前提前激活")
print(
    f"\n结论：现状={r1}  提前激活={r2}  → "
    f"{'提前激活有效，B2 修法可行' if (r2 and not r1) else ('两者都生效，B2 收益需再判' if r2 and r1 else '提前激活未达预期，方案①需调整')}"
)
