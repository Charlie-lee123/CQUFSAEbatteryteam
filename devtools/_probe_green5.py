# -*- coding: utf-8 -*-
"""探针 5：真窗口喂真实布局数据，逐像素定位绿色落到哪些文本行。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QPainter, QImage
from PySide6.QtWidgets import QApplication

from ui_theme import apply_scheme, ui_font, qcolor


ROUND = ("[2026-10-12 23:00:00]\n"
         "Voltage1:\n"
         "01.2500 02.2500 03.2600 04.3800 05.4600\n"
         "Temperature1:\n"
         "1 1.22 2 1.23 3 1.19 4 1.30 5 1.28\n"
         "BMS OK\n")


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))
    apply_scheme(app)

    from main_window import MainWindow
    win = MainWindow()
    win.resize(900, 700)
    win.show()
    app.processEvents()

    # 直接走显示入口（等于 _flush_rx 之后的路径，喂三轮）
    text = ROUND + ROUND.replace("23:00:00", "23:00:01") + \
        ROUND.replace("23:00:00", "23:00:02")
    win._emit_block(text)
    app.processEvents()

    green = qcolor("volt_low").name().upper()
    # 接收区渲染成图，找绿色像素行 → 对应文本块
    vp = win.txt_rx.viewport()
    img = QImage(vp.size(), QImage.Format_ARGB32)
    img.fill(0xFFFFFFFF)
    vp.render(img)
    rows = []
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y).name().upper() == green:
                for row in rows:
                    if abs(row[0] - y) <= 4:
                        row[1] = max(row[1], y)
                        break
                else:
                    rows.append([y, y])
                break
    # 逐视觉行找文本：用 cursorRect 反查
    from PySide6.QtGui import QTextCursor
    line_h = win._single_line_height()
    doc = win.txt_rx.document()
    print("green visual rows:", rows)
    for y0, y1 in rows:
        mid = (y0 + y1) // 2
        cur = win.txt_rx.cursorForPosition(win.txt_rx.viewport().mapFrom(
            win.txt_rx.viewport(), __import__("PySide6.QtCore", fromlist=["QPoint"]).QPoint(2, mid)))
        blk = cur.block()
        print(f"  y={y0}-{y1} -> block {blk.blockNumber()}: {blk.text()!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
