# -*- coding: utf-8 -*-
"""只读几何探针：量设置面板里几处控件的真实宽度。

要回答四个问题（用户 2026-11-27 四张截图）：
  1. 「材质效果」滑杆 vs 「动画帧率」滑杆 —— 各多宽？差多少？
  2. 「动画帧率」那一行去掉复原按钮后，滑杆能长到多宽？
  3. 「赛季」下拉框里 "2026" 有没有被截（内容区宽 vs 文字宽）？
  4. 接收区「显示时间戳 / HEX 显示」两个开关的几何。

不改产品源码；QSettings 只读，不写。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionComboBox  # noqa: E402


def main() -> None:
    app = QApplication(sys.argv)
    import ui_theme
    ui_theme.set_scheme("dark")
    import customization  # noqa: F401
    from ui_settings import SettingsDialog

    dlg = SettingsDialog("dark", 55)
    dlg.show()
    app.processEvents()
    dlg.resize(dlg.width(), 900)
    app.processEvents()

    print("=" * 66)
    print(f"面板宽 {dlg.width()}  表单几何 {dlg.form.geometry().getRect()}")

    # ---- 1/2) 两根滑杆 ----
    glass = dlg.slider_glass
    fps = dlg._custom_widgets["anim_fps"]
    print("-" * 66)
    print(f"[材质效果] 滑杆 {glass.width()}  数值标签 {dlg.lbl_glass.width()}"
          f"  整行容器 {dlg.lbl_glass.parentWidget().width()}")
    print(f"[动画帧率] 滑杆 {fps.width()}  行容器 "
          f"{dlg._custom_rows['anim_fps'][1].width()}")

    fps_field = dlg._custom_rows["anim_fps"][1]
    lay = fps_field.layout()
    print(f"  anim_fps 行内 spacing={lay.spacing()} 成员：")
    total = 0
    for i in range(lay.count()):
        w = lay.itemAt(i).widget()
        if w is None:
            print(f"    [{i}] (spacer)")
            continue
        print(f"    [{i}] {type(w).__name__:12s} name={w.accessibleName()!r:14s}"
              f" w={w.width():4d}")
        total += w.width()
    print(f"    行内控件宽之和 = {total}")

    # 去掉复原按钮后滑杆能拿到的宽度（行内控件宽之和 - 数值标签 - 一个 spacing）
    from ui_controls import IconButton
    resets = [b for b in fps_field.findChildren(IconButton)
              if b.accessibleName() == "恢复默认值"]
    for b in resets:
        print(f"  复原按钮 w={b.width()} visible={b.isVisible()}")
    if resets:
        freed = resets[0].width() + lay.spacing()
        print(f"  → 去掉复原按钮可释放 {freed}px，滑杆将变为 ~{fps.width() + freed}")

    # ---- 3) 赛季下拉 ----
    print("-" * 66)
    season = dlg.spin_vehicle_season
    opt = QStyleOptionComboBox()
    opt.initFrom(season)
    opt.currentText = season.currentText()
    cr = season.style().subControlRect(
        QStyle.ComplexControl.CC_ComboBox, opt,
        QStyle.SubControl.SC_ComboBoxEditField, season)
    fm = season.fontMetrics()
    print(f"[赛季] 框宽 {season.width()}  条目数 {season.count()}"
          f"  首项 {season.itemText(0)}  末项 {season.itemText(season.count() - 1)}")
    print(f"  内容区宽 {cr.width()}  文字宽 {fm.horizontalAdvance(season.currentText())}"
          f"  当前值 {season.currentText()!r}")
    print(f"  是否截断: {fm.horizontalAdvance(season.currentText()) > cr.width()}")

    # ---- 4) 主窗口两个开关 ----
    print("-" * 66)
    from PySide6.QtCore import QSettings
    _ = QSettings  # 只提一下，不写
    import main_window as mw

    win = mw.MainWindow()
    win.show()
    app.processEvents()
    for name in ("chk_timestamp", "chk_hex_view"):
        sw = getattr(win, name)
        print(f"[{name}] text={sw.text()!r} size={sw.width()}x{sw.height()}"
              f" hint={sw.sizeHint().width()}")
    win.close()
    dlg.close()
    app.processEvents()
    print("=" * 66)
    print("DONE")


if __name__ == "__main__":
    main()