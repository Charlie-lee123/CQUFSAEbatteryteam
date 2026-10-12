# -*- coding: utf-8 -*-
"""出图：设置面板数字框「修复前 vs 修复后」（同一份代码，开关式）。

before：关掉实测墨迹摆位（走旧的字号线性兜底）→ "4500 mV" 这批贴底 4.5px，
正是用户截图里的样子。after：实测摆位，上下留白相等。
⚠️ 必须 QT_QPA_PLATFORM=windows 跑。
用法：before / after / compose
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication, QLabel

KEYS = ("volt_limit_low", "volt_limit_high", "temp_limit_low", "temp_limit_high")
ROW_FILES = {"before": "spin_rows_before.png", "after": "spin_rows_after.png"}


def render_rows(state: str) -> None:
    import app_log

    app_log.init()
    import ui_controls as uc
    from ui_settings import SettingsDialog
    from ui_theme import apply_scheme, current_scheme, ui_font

    app = QApplication.instance() or QApplication(sys.argv)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    original = current_scheme()
    app.setStyle("Fusion")
    apply_scheme("dark")
    app.setFont(ui_font(12.5))

    if state == "before":
        uc._edit_ink_offset = lambda edit: None

    dialog = SettingsDialog("dark", 0)
    try:
        dialog.show()
        for _ in range(25):
            app.processEvents()
        from PySide6.QtWidgets import QScrollArea
        scroll = dialog.findChildren(QScrollArea)[0]
        scroll.ensureWidgetVisible(dialog._custom_widgets[KEYS[0]], 0, 100)
        for _ in range(15):
            app.processEvents()

        row_images = []
        for key in KEYS:
            spin = dialog._custom_widgets[key]
            lab = spin.parentWidget().findChildren(QLabel)
            name = next((l for l in lab
                         if l.text() in ("电压下限", "电压上限", "温度下限", "温度上限")
                         and l.geometry().intersects(spin.geometry())), None)
            li = name.grab().toImage() if name is not None else None
            ci = spin.grab().toImage()
            pad = 20
            w = (li.width() if li is not None else 0) + pad + ci.width()
            h = max(ci.height(), li.height() if li is not None else 0)
            row = QImage(w, h, QImage.Format.Format_ARGB32)
            row.fill(QColor("#0E0E10"))
            p = QPainter(row)
            x = 0
            if li is not None:
                p.drawImage(0, (h - li.height()) // 2, li)
                x = li.width() + pad
            p.drawImage(x, (h - ci.height()) // 2, ci)
            p.end()
            row_images.append(row)

        gap = 8
        out = QImage(max(r.width() for r in row_images),
                     sum(r.height() for r in row_images) + gap * (len(row_images) + 1),
                     QImage.Format.Format_ARGB32)
        out.fill(QColor("#0E0E10"))
        p = QPainter(out)
        y = gap
        for r in row_images:
            p.drawImage(0, y, r)
            y += r.height() + gap
        p.end()
        path = os.path.join(ROOT, "_check", ROW_FILES[state])
        out.save(path)
        print("saved", path, out.width(), "x", out.height())
    finally:
        dialog.close()
        app.processEvents()
        apply_scheme(original)
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()


def compose() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    before = QImage(os.path.join(ROOT, "_check", ROW_FILES["before"]))
    after = QImage(os.path.join(ROOT, "_check", ROW_FILES["after"]))
    target_w = 620
    b = before.scaled(target_w, int(before.height() * target_w / before.width()),
                      Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
    a = after.scaled(target_w, int(after.height() * target_w / after.width()),
                     Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    margin, gap_x, label_h = 28, 30, 56
    canvas = QImage(margin * 2 + target_w * 2 + gap_x,
                    margin * 2 + label_h + max(b.height(), a.height()),
                    QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#0A0A0C"))
    p = QPainter(canvas)
    font = QFont("Microsoft YaHei UI")
    font.setPointSizeF(14)
    font.setBold(True)
    p.setFont(font)
    p.setPen(QColor("#E8E9EB"))
    p.drawText(margin + 195, margin + 36, "修复前")
    p.drawText(margin * 2 + target_w + gap_x + 215, margin + 36, "修复后")
    p.drawImage(margin, margin + label_h, b)
    p.drawImage(margin * 2 + target_w + gap_x, margin + label_h, a)
    p.end()
    out = os.path.join(ROOT, "_check", "spin_before_after.png")
    canvas.save(out)
    print("saved", out, canvas.width(), "x", canvas.height())


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "compose"
    if mode in ROW_FILES:
        render_rows(mode)
    else:
        compose()
