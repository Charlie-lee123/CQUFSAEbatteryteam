# -*- coding: utf-8 -*-
"""出图：串口配置栏「修复前 vs 修复后」对比（同一份代码，开关式）。

做法：`before` 模式在进程里把本轮的三处修复机制**临时关掉**（实测墨迹测量、
非编辑下拉的实例矫正、旧的左端目标值），重摆一次再渲染；`after` 模式原样
渲染。两者几何、字体、DPI 完全一致 —— 像素级可比。

⚠️ 必须 QT_QPA_PLATFORM=windows 跑（offscreen 中文渲染成方框，踩过）。

用法：
    python devtools/_shot_serial_compare.py before
    python devtools/_shot_serial_compare.py after
    python devtools/_shot_serial_compare.py compose   # 拼图
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

ROW_LABELS = ("波特率", "停止位", "数据位", "校验位", "保留行数")
ROW_FILES = {"before": "serial_rows_before.png", "after": "serial_rows_after.png"}
SERIAL_CTLS = ("cmb_baud", "cmb_stopbits", "cmb_bytesize", "cmb_parity",
               "spin_rx_lines")


def render_rows(state: str) -> None:
    import app_log

    app_log.init()
    import ui_controls as uc
    from main_window import MainWindow
    from ui_theme import apply_scheme, current_scheme, ui_font

    app = QApplication.instance() or QApplication(sys.argv)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    original = current_scheme()
    app.setStyle("Fusion")
    apply_scheme("dark")
    app.setFont(ui_font(12.5))

    if state == "before":
        # ---- 关掉本轮的三处修复，复原到"修复前"的渲染 ----
        uc._edit_ink_offset = lambda edit: None            # 数字框/可编辑下拉不实测
        uc._combo_label_ink_offset = lambda combo: None    # 非编辑下拉不实测
        uc.ChevronSpinBox._text_top_compensation = (
            lambda self: max(0, int(round((self.font().pointSizeF() - 9.0) * 0.8))))
        uc.ChevronSpinBox.TEXT_MARGIN = (7, 0, 6, 0)
        uc.ChevronComboBox.EDIT_TARGET_X = 7

    window = MainWindow()
    try:
        if state == "before":
            for name in SERIAL_CTLS:
                ctl = getattr(window, name)
                ctl.setStyleSheet("")
                ctl._fit_shift = 0
                ctl._fit_key = None
                if isinstance(ctl, uc.ChevronSpinBox):
                    ctl.findChild(QLineEdit).setTextMargins(7, 0, 6, 0)
        window.showNormal()
        window.show()
        for _ in range(25):
            app.processEvents()
        for name in SERIAL_CTLS:
            getattr(window, name)._place_edit()
        for _ in range(10):
            app.processEvents()

        labels = {lab.text(): lab for lab in window.findChildren(QLabel)
                  if lab.text() in ROW_LABELS}
        row_images = []
        for name, ctl_name in zip(ROW_LABELS, SERIAL_CTLS):
            lab = labels[name]
            ctl = getattr(window, ctl_name)
            li = lab.grab().toImage()
            ci = ctl.grab().toImage()
            w = li.width() + 20 + ci.width()   # 20 = 布局 row 间距（逻辑 10 × dpr2）
            h = max(li.height(), ci.height())
            row = QImage(w, h, QImage.Format.Format_ARGB32)
            row.fill(QColor("#0E0E10"))
            p = QPainter(row)
            p.drawImage(0, (h - li.height()) // 2, li)
            p.drawImage(li.width() + 20, (h - ci.height()) // 2, ci)
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
        window.close()
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
    target_w = 560
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
    p.drawText(margin + 178, margin + 36, "修复前")
    p.drawText(margin * 2 + target_w + gap_x + 200, margin + 36, "修复后")
    p.drawImage(margin, margin + label_h, b)
    p.drawImage(margin * 2 + target_w + gap_x, margin + label_h, a)
    p.end()
    out = os.path.join(ROOT, "_check", "serial_before_after.png")
    canvas.save(out)
    print("saved", out, canvas.width(), "x", canvas.height())


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "compose"
    if mode in ROW_FILES:
        render_rows(mode)
    else:
        compose()
