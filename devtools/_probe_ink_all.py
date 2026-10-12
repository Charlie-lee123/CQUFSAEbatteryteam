# -*- coding: utf-8 -*-
"""探针：全控件文字墨迹的上/下/左留白 —— 一次量清楚哪些控件"偏上/偏下"。

⚠️ 必须像 main.py 一样先 setStyle("Fusion") + apply_scheme + setFont(ui_font(12.5))：
不设应用字体的探针里，所有"继承默认字体"的控件会退化成系统 9pt，量到的
不是真实界面（踩过：设置面板数字框、非编辑下拉框全被量成 9pt YaHei）。

墨迹检测：先在**竖直中段**扫出"文字列"（避开左右边框/圆角/chevron），
再只用这些列扫行 —— 圆角弧线在 x 很小的位置会制造假墨迹，先列后行能绕开。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QScrollArea


def ink_box(widget, exclude_right: int = 30) -> tuple | None:
    """返回 (上留白, 下留白, 左留白, 控件高, 控件宽)——逻辑像素；量不到返回 None。

    exclude_right：右侧多少逻辑像素内不算文字（chevron / 步进箭头所在区）。
    """
    pix = widget.grab()
    dpr = pix.devicePixelRatio() or 1.0
    img = pix.toImage()
    w, h = img.width(), img.height()
    x_hi = max(w - int(exclude_right * dpr), 1)

    # 1) 文字列：竖直中段（30%~70%）内有明暗差的列
    y0, y1 = int(h * 0.30), int(h * 0.70)
    cols = []
    for x in range(int(4 * dpr), x_hi):
        vals = [img.pixelColor(x, y).lightness() for y in range(y0, y1)]
        if vals and (max(vals) - min(vals)) > 60:
            cols.append(x)
    if not cols:
        return None
    x0, x1 = min(cols), max(cols)

    # 2) 文字行：只用文字列扫全高
    rows = []
    for y in range(h):
        vals = [img.pixelColor(x, y).lightness() for x in range(x0, x1 + 1)]
        if vals and (max(vals) - min(vals)) > 60:
            rows.append(y)
    if not rows:
        return None
    top = rows[0] / dpr
    bottom = h / dpr - (rows[-1] + 1) / dpr
    return (top, bottom, x0 / dpr, h / dpr, w / dpr)


def report(name: str, widget, exclude_right: int = 30) -> None:
    box = ink_box(widget, exclude_right=exclude_right)
    font = widget.font()
    fam = font.family().split(",")[0]
    if box is None:
        print(f"  {name:26s} ★量不到墨迹")
        return
    t, b, l, h, w = box
    delta = b - t
    flag = "✓" if abs(delta) <= 1.0 else ("★偏上" if delta > 0 else "★偏下")
    print(f"  {name:26s} {fam[:14]:14s}{font.pointSizeF():5.1f}pt "
          f"{w:5.0f}x{h:4.0f}  上{t:5.1f} 下{b:5.1f} 左{l:5.1f}  "
          f"Δ={delta:+5.1f} {flag}")


def main() -> None:
    import app_log

    app_log.init()
    from main_window import MainWindow
    from ui_settings import SettingsDialog
    from ui_theme import apply_scheme, current_scheme, ui_font

    app = QApplication.instance() or QApplication(sys.argv)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    original = current_scheme()
    # 与 main.py 完全一致的起手
    app.setStyle("Fusion")
    apply_scheme("dark")
    app.setFont(ui_font(12.5))
    window = MainWindow()
    dialog = None
    try:
        window.showNormal()
        window.show()
        for _ in range(20):
            app.processEvents()

        print("=== 主窗口 · 串口配置栏 ===")
        for name, w in (("波特率(可编辑)", window.cmb_baud),
                        ("停止位", window.cmb_stopbits),
                        ("数据位", window.cmb_bytesize),
                        ("校验位", window.cmb_parity),
                        ("保留行数(spin)", window.spin_rx_lines)):
            report(name, w)

        print("\n=== 主窗口 · 其他 ===")
        for name, w in (("间隔(发送区 spin)", window.spin_interval),
                        ("时钟标签", window.lbl_clock,),
                        ("计数标签", window.lbl_count)):
            report(name, w if not isinstance(w, tuple) else w[0])

        dialog = SettingsDialog("dark", 0)
        dialog.show()
        for _ in range(25):
            app.processEvents()
        scroll = dialog.findChildren(QScrollArea)[0]
        print("\n=== 设置面板 · 定制数字框 ===")
        scroll.ensureWidgetVisible(dialog._custom_widgets["volt_limit_high"], 0, 100)
        for _ in range(10):
            app.processEvents()
        for key in ("pause_snap_lines", "volt_limit_low", "volt_limit_high",
                    "temp_limit_low", "temp_limit_high"):
            report(key, dialog._custom_widgets[key])

        print("\n=== 设置面板 · 滑杆行 ===")
        lab = getattr(dialog, "_custom_value_labels", {}).get("rx_font_size")
        if lab is not None:
            report("rx_font_size 数值标签", lab)
        prev = dialog._custom_previews.get("rx_font_size")
        if prev is not None:
            report("rx_font_size 预览", prev)

        print("\n=== 设置面板 · 按钮（InkButton 自绘）===")
        bar = scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        for _ in range(15):
            app.processEvents()
        report("导出日志", dialog.btn_export_log)
        report("检查更新", dialog.btn_check_update)
        for child in dialog.findChildren(type(dialog.btn_export_log)):
            if child.text() in ("取消", "确定"):
                report(child.text(), child)
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
