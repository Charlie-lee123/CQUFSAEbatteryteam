# -*- coding: utf-8 -*-
"""探针：① 串口配置栏各控件文字左边缘是否对齐 ② Switch 命中区范围。

只读，可在主工作区安全运行（自带 QSettings 快照还原）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPoint, QSettings
from PySide6.QtWidgets import QApplication


def ink_left(widget) -> float | None:
    """文字墨迹最左的 x（逻辑像素），量不到返回 None。

    ⚠️ 从 6×dpr 起扫：左边框那 1px 描边本身就与背景有色差，会被当成"墨迹"。
    """
    pix = widget.grab()
    dpr = pix.devicePixelRatio() or 1.0
    img = pix.toImage()
    w, h = img.width(), img.height()
    y0, y1 = int(h * 0.25), int(h * 0.75)
    for x in range(int(6 * dpr), w):
        vals = [img.pixelColor(x, y).lightness() for y in range(y0, y1)]
        if vals and (max(vals) - min(vals)) > 60:
            return x / dpr
    return None


def main() -> None:
    import app_log

    app_log.init()
    from main_window import MainWindow
    from ui_settings import SettingsDialog
    from ui_theme import apply_scheme

    app = QApplication.instance() or QApplication(sys.argv)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    apply_scheme("light")
    window = MainWindow()
    dialog = None
    try:
        window.showNormal()
        window.show()
        for _ in range(20):
            app.processEvents()

        print("=== 串口配置栏：文字左边缘 ===")
        rows = (("波特率(可编辑下拉)", window.cmb_baud),
                ("停止位(下拉)", window.cmb_stopbits),
                ("数据位(下拉)", window.cmb_bytesize),
                ("校验位(下拉)", window.cmb_parity),
                ("保留行数(数字框)", window.spin_rx_lines))
        lefts = []
        for name, ctl in rows:
            left = ink_left(ctl)
            lefts.append(left)
            shown = "量不到" if left is None else f"{left:.1f}"
            extra = ""
            edit = ctl.lineEdit() if hasattr(ctl, "lineEdit") else None
            if edit is not None:
                m = edit.textMargins()
                extra = (f"  QLineEdit x={edit.x()} w={edit.width()} "
                         f"textMargin L={m.left()}")
            print(f"  {name:22s} 文字左边缘 x={shown:>7s}  控件宽={ctl.width()}{extra}")
        if all(v is not None for v in lefts):
            spread = max(lefts) - min(lefts)
            print(f"  → {'左端对齐 ✓' if spread <= 2.0 else '★左端未对齐'}"
                  f"（最大差 {spread:.1f}px）")

        print("\n=== Switch 命中区 ===")
        dialog = SettingsDialog("light", 0)
        dialog.show()
        for _ in range(20):
            app.processEvents()
        toggle = dialog._custom_widgets["startup_maximized"]
        track = toggle._track_rect()
        print(f"  控件 {toggle.width()}x{toggle.height()}  "
              f"轨道 x=[{track.left():.0f},{track.right():.0f}] "
              f"y=[{track.top():.0f},{track.bottom():.0f}]  文字={toggle.text()!r}")
        mid = toggle.height() // 2
        for point, desc in ((QPoint(5, mid), "轨道左端"),
                            (QPoint(20, mid), "轨道中段"),
                            (QPoint(int(track.right()) - 2, mid), "轨道右端"),
                            (QPoint(int(track.right()) + 4, mid), "轨道右侧留白"),
                            (QPoint(int(track.right()) + 40, mid), "更右侧空白"),
                            (QPoint(5, 2), "轨道上方留白"),
                            (QPoint(5, toggle.height() - 2), "轨道下方留白")):
            print(f"  {desc:14s} {str(point.toTuple()):>12s}"
                  f" -> hitButton={toggle.hitButton(point)}")
    finally:
        if dialog is not None:
            dialog.close()
        window.close()
        app.processEvents()
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()


if __name__ == "__main__":
    main()
