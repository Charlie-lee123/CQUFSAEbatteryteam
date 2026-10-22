# -*- coding: utf-8 -*-
r"""QA 独立探针：设置面板「车辆（电池箱）」一节的显隐 / 对齐 / 横向溢出。

验四件事（全部是断言，不是打印）：
  1. 3 模组车 → 逐模组行 = 3，且「模组 i」行标签左缘与本节其它行标签**同一 x**；
  2. 模组个数改 5 → 5 行；改回 0 → 逐模组行消失、旧三件套（模组个数/每模组电芯数/
     每模组温度采集数）重新可见；
  3. 删当前车 → 下拉显示「（未选择）」且 active_profile() is None；
  4. body 宽 == 滚动区视口宽、横向滚动条 range 为 0（没有偷偷横向滚）。

用法：QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_qa_settings_vehicle.py
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QPoint, QSettings                   # noqa: E402
from PySide6.QtWidgets import (QApplication, QMessageBox,           # noqa: E402
                               QScrollArea)

FAILS: list[str] = []


def pump(app, ms: int) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.003)


def x_of(widget, dialog) -> int:
    return widget.mapTo(dialog, QPoint(0, 0)).x()


def dump_rows(dialog, tag: str) -> None:
    print(f"\n[{tag}] 下拉={dialog.cmb_vehicle.currentText()!r} "
          f"模组个数框={dialog.spin_vehicle_modules.value()} "
          f"逐模组行={len(dialog._module_rows)}")
    print("       旧三件套可见 =",
          {key: dialog._custom_rows[key][1].isVisibleTo(dialog)
           for key in dialog._LEGACY_GRID_KEYS})
    if dialog._module_rows:
        print("       模组行标签 =", [label.text() for label, _ in dialog._module_rows])


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, ui_font
    apply_scheme("dark")
    app.setFont(ui_font(12.5))

    import customization
    from selftest import restore_settings
    from ui_settings import SettingsDialog

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    dialog = None
    try:
        for name in customization.vehicle_names():
            customization.delete_vehicle(name)
        customization.save_vehicle("QA-3模组", [25, 24, 25], [8, 5, 6])
        customization.select_vehicle("QA-3模组")
        customization.save_vehicle("QA-备车", [10, 10], [4, 4])

        dialog = SettingsDialog("dark", 55)
        dialog.setGeometry(60, 30, 560, 880)
        dialog.show()
        pump(app, 500)
        dump_rows(dialog, "初始（3 模组车已选）")

        if len(dialog._module_rows) != 3:
            FAILS.append(f"3 模组车应当有 3 行逐模组控件，实际 {len(dialog._module_rows)}")
        xs = {label.text(): x_of(label, dialog) for label, _ in dialog._module_rows}
        # 2026-10-22 起「车辆名称」并进了选车框（就地改名），这一节只剩两行
        ref = {"车辆": x_of(dialog._vehicle_rows["pick"][0], dialog),
               "模组个数": x_of(dialog._vehicle_rows["modules"][0], dialog)}
        print("       标签左缘 x：", ref, xs)
        if len(set(xs.values())) != 1:
            FAILS.append(f"逐模组行标签左缘不齐：{xs}")
        if set(xs.values()) != set(ref.values()):
            FAILS.append(f"逐模组行标签与本节其它行左缘不一致：{xs} vs {ref}")

        # ---- 模组个数 5 ----
        dialog.spin_vehicle_modules.setValue(5)
        pump(app, 300)
        dump_rows(dialog, "模组个数 = 5")
        if len(dialog._module_rows) != 5:
            FAILS.append(f"模组个数 5 时应当 5 行，实际 {len(dialog._module_rows)}")
        xs5 = {x_of(label, dialog) for label, _ in dialog._module_rows}
        print("       5 行标签左缘 x =", xs5,
              " 字段右缘 =", [field.geometry().right() for _, field in dialog._module_rows][:5],
              " body 宽 =", dialog.width())

        # ---- 改回 0：逐模组行消失、旧三件套露面 ----
        dialog.spin_vehicle_modules.setValue(0)
        pump(app, 300)
        dump_rows(dialog, "模组个数 = 0")
        if dialog._module_rows:
            FAILS.append("模组个数 0 时逐模组行应当消失")
        legacy_visible = [dialog._custom_rows[key][1].isVisibleTo(dialog)
                          for key in dialog._LEGACY_GRID_KEYS]
        if not all(legacy_visible):
            FAILS.append(f"模组个数 0 时旧三件套必须重新可见：{legacy_visible}")
        if customization.active_profile() is not None:
            FAILS.append("模组个数 0 时 active_profile 必须是 None")

        # ---- 删除当前车（把确认框替身成 Yes，走真实 UI 路径）----
        customization.select_vehicle("QA-3模组")
        dialog._sync_vehicle_rows()
        pump(app, 200)
        original_question = QMessageBox.question
        QMessageBox.question = staticmethod(
            lambda *a, **k: QMessageBox.StandardButton.Yes)
        try:
            dialog._on_vehicle_delete()
        finally:
            QMessageBox.question = original_question
        pump(app, 300)
        dump_rows(dialog, "删掉当前车之后")
        print("       active_profile() =", customization.active_profile())
        print("       下拉条目 =", [dialog.cmb_vehicle.itemText(i)
                                for i in range(dialog.cmb_vehicle.count())])
        if dialog.cmb_vehicle.currentText() != "（未选择）":
            FAILS.append(f"删当前车后下拉应显示（未选择），实际 "
                         f"{dialog.cmb_vehicle.currentText()!r}")
        if customization.active_profile() is not None:
            FAILS.append("删当前车后 active_profile 应为 None")
        if dialog._module_rows:
            FAILS.append("删当前车后不该还有逐模组行")
        if customization.current_vehicle() != "":
            FAILS.append("删当前车后 current_vehicle 应为空")

        # ---- 横向溢出 ----
        scroll = dialog.findChild(QScrollArea)
        body = scroll.widget()
        viewport = scroll.viewport()
        hbar = scroll.horizontalScrollBar()
        print("\n=== 宽度 ===")
        print(f"  body {body.width()} / 视口 {viewport.width()} / "
              f"body 最小宽 {body.minimumSizeHint().width()} / "
              f"横向滚动条 [{hbar.minimum()}, {hbar.maximum()}] "
              f"visible={hbar.isVisible()}")
        if body.width() != viewport.width():
            FAILS.append(f"body 宽 {body.width()} != 视口宽 {viewport.width()}")
        if hbar.maximum() != hbar.minimum():
            FAILS.append(f"横向滚动条有范围：{hbar.minimum()}..{hbar.maximum()}")
        if body.minimumSizeHint().width() > viewport.width():
            FAILS.append(f"body 最小宽 {body.minimumSizeHint().width()} "
                         f"> 视口 {viewport.width()}：窗口再窄会横向溢出")

        # 面板最小宽 524 时再看一次（最窄状态最容易横向溢出）
        dialog.resize(dialog.minimumWidth(), 880)
        pump(app, 400)
        print(f"  最小宽 {dialog.minimumWidth()}：body {body.width()} / "
              f"视口 {viewport.width()} / 横条 {hbar.maximum() - hbar.minimum()}")
        if body.width() != viewport.width():
            FAILS.append(f"最小宽时 body {body.width()} != 视口 {viewport.width()}")
        if hbar.maximum() != hbar.minimum():
            FAILS.append(f"最小宽时横向滚动条有范围 {hbar.minimum()}..{hbar.maximum()}")
    finally:
        if dialog is not None:
            dialog.close()
            app.processEvents()
        pump(app, 150)
        restore_settings(settings, snapshot)
        settings.sync()

    print("\n================ QA 判定 ================")
    if FAILS:
        for item in FAILS:
            print("FAIL  " + item)
        return 1
    print("PASS  面板行数跟随/左缘对齐/旧三件套露隐/删车清空/无横向溢出 全部成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
