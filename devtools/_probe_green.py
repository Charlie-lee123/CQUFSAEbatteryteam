# -*- coding: utf-8 -*-
"""探针：欠压绿是否越界到温度段。

QTextDocument + RxHighlighter，喂真实布局（时间戳 → 电压段含欠压 →
Temperature1: → 温度段 → BMS），逐块逐字符 dump 前景色，标出绿色落点。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QTextCharFormat, QTextDocument

from ui_theme import apply_scheme, qcolor, ui_font


def main() -> int:
    app = __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))
    apply_scheme(app)

    import customization
    customization.migrate_defaults()
    print("volt limits:", customization.get("volt_limit_low"),
          customization.get("volt_limit_high"))

    import ui_highlight as H

    doc = QTextDocument()
    hl = H.RxHighlighter(doc)
    hl.refresh()

    low_green = qcolor("volt_low")
    data = "\n".join([
        "[2026-10-12 23:00:00]",
        "Voltage1:",
        "01.2500 02.2500 03.2600",          # 两条欠压 → 绿
        "04.3800 05.4600",                  # 一条超高 → 红
        "Temperature1:",
        "1 1.22 2 1.23 3 1.19",             # 温度段（不应有任何绿）
        "4 1.30 5 1.28",
        "BMS OK",
        "[2026-10-12 23:00:02]",
        "01.2500 02.3800",
        "Temperature1:",
        "1 1.22 2 1.25",
        "BMS OK",
    ])
    doc.setPlainText(data)

    low_name = low_green.name().upper()
    print("volt_low color:", low_name)
    from PySide6.QtGui import QImage
    doc.setDocumentMargin(4.0)
    doc.setTextWidth(600.0)
    img = QImage(int(doc.size().width()) + 8, int(doc.size().height()) + 8,
                 QImage.Format_ARGB32)
    img.fill(0xFFFFFFFF)
    from PySide6.QtGui import QPainter
    p = QPainter(img)
    doc.drawContents(p)
    p.end()
    rows = []
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.name().upper() == low_name:
                # 粗定位到行：按 y 聚类
                for row in rows:
                    if abs(row[0] - y) <= 3:
                        row[1] = max(row[1], x)
                        break
                else:
                    rows.append([y, x])
    for y, xmax in rows:
        print(f"green pixels near y={y} up to x={xmax}")
    print("total green rows:", len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
