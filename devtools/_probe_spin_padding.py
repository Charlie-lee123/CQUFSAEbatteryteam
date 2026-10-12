# -*- coding: utf-8 -*-
"""探针：输入框内文字与上下边框的间距（找"上下不相等"的那个）。

只读，可在主工作区安全运行（自带 QSettings 快照还原）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


def ink_extent(widget, x_from: float = 0.12, x_to: float = 0.70) -> tuple:
    """渲染控件，返回文字墨迹的 (上留白, 下留白, 高, 墨迹高)——逻辑像素。

    ⚠️ x 范围要避开右侧的箭头/按钮（下拉框的 chevron 竖直居中，会把墨迹的
    上下范围整个撑开）—— 量下拉框时把 x_to 收到 0.55 左右。
    """
    pix = widget.grab()
    dpr = pix.devicePixelRatio() or 1.0
    img = pix.toImage()
    w, h = img.width(), img.height()
    rows = []
    for y in range(h):
        vals = [img.pixelColor(x, y).lightness()
                for x in range(int(w * x_from), int(w * x_to))]
        if vals and (max(vals) - min(vals)) > 60:
            rows.append(y)
    if not rows:
        return (None, None, h / dpr, 0)
    top = rows[0] / dpr
    bottom = rows[-1] / dpr
    return (top, h / dpr - bottom, h / dpr, bottom - top)


def main() -> None:
    import customization
    from main_window import MainWindow
    from ui_settings import SettingsDialog
    from ui_theme import apply_scheme, current_scheme

    app = QApplication.instance() or QApplication(sys.argv)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    original = current_scheme()
    window = MainWindow()
    dialog = None
    try:
        apply_scheme("light")
        window.resize(1100, 800)
        window.showNormal()
        window.show()
        for _ in range(20):
            app.processEvents()

        spin = window.spin_rx_lines
        print("=== 串口配置栏「保留行数」 ===")
        print(f"  字体 {spin.font().family()!r} {spin.font().pointSizeF()}pt "
              f"控件高 {spin.height()} 行内编辑框 {spin.findChild(type(spin).__mro__[0]).geometry() if False else ''}")
        edit = spin.findChild(__import__("PySide6.QtWidgets", fromlist=["QLineEdit"]).QLineEdit)
        if edit is not None:
            print(f"  行内编辑框 geometry={edit.geometry().getRect()} "
                  f"textMargins={edit.textMargins().left()},{edit.textMargins().top()},"
                  f"{edit.textMargins().right()},{edit.textMargins().bottom()}")
        top, bottom, height, ink = ink_extent(spin)
        print(f"  墨迹：上留白 {top:.1f} / 下留白 {bottom:.1f} / 框高 {height:.1f} / 墨迹高 {ink:.1f}"
              f"  → {'上下相等' if top is not None and abs(top - bottom) <= 1.0 else '★上下不等'}")

        dialog = SettingsDialog("light", 0)
        dialog.show()
        for _ in range(20):
            app.processEvents()
        print("\n=== 同栏的其他控件（串口配置栏，同为 11.5pt）===")
        for name, widget in (("波特率下拉框", window.cmb_baud),
                             ("校验位下拉框", window.cmb_parity)):
            t, b, h, ink = ink_extent(widget, 0.10, 0.55)
            print(f"  {name}: 字体 {widget.font().pointSizeF()}pt "
                  f"上留白 {t:.1f} / 下留白 {b:.1f} / 框高 {h:.1f} / 墨迹高 {ink:.1f}"
                  f"  → {'上下相等' if t is not None and abs(t - b) <= 1.0 else '★上下不等'}")

        print("\n=== 设置面板的对照输入框 ===")
        for key in ("volt_limit_high", "temp_limit_high"):
            w = dialog._custom_widgets[key]
            edit = w.findChild(__import__("PySide6.QtWidgets", fromlist=["QLineEdit"]).QLineEdit)
            margins = (f"{edit.textMargins().left()},{edit.textMargins().top()},"
                       f"{edit.textMargins().right()},{edit.textMargins().bottom()}"
                       if edit is not None else "-")
            t, b, h, ink = ink_extent(w)
            print(f"  {key}: 字体 {w.font().pointSizeF()}pt 边距 {margins} "
                  f"上留白 {t:.1f} / 下留白 {b:.1f} / 框高 {h:.1f}"
                  f"  → {'上下相等' if t is not None and abs(t - b) <= 1.0 else '★上下不等'}")
    finally:
        if dialog is not None:
            dialog.close()
        window.close()
        app.processEvents()
        apply_scheme(original)
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()


if __name__ == "__main__":
    main()
