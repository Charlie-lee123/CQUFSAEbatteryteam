# -*- coding: utf-8 -*-
r"""QA 找茬探针：selftest.main() 那句 ``custom/vehicle=""`` 到底有没有生效。

背景：selftest.main() 的注释说「选了车的机器也不该影响自测，所以这里连当前车辆
一起清空 —— 否则 active_profile() 会压过这三个 0」，做法是
``global_settings.setValue("custom/vehicle", "")``。

但 customization._load_store() 的判据是：
    先看 custom/vehicles JSON 里的 current（合法就用它）→ 只有它不合法才退回
    custom/vehicle 镜像键。
所以只清镜像键**改不动**当前选择 —— 本探针把这条链量出来，并实际跑那三条
「前提是一段数据在同一行」的用例，看会不会假挂。

用法：QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_qa_selftest_hygiene.py
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings                              # noqa: E402
from PySide6.QtWidgets import QApplication                        # noqa: E402

CHECKS = ("回车发送/时间戳轮首/序号染蓝", "收包合并与暂停继续", "定位最近异常按钮")


def pump(app, ms: int) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.003)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import ui_font
    app.setFont(ui_font(12.5))

    import customization
    import selftest

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    failures: list[str] = []
    try:
        # ---- 模拟"用户配了一台车并选中" ----
        # QA_CELLS=1,1,1（默认，能真正触发拆行）/ 25,24,25（常见真实车型）
        cells = [int(x) for x in os.environ.get("QA_CELLS", "1,1,1").split(",")]
        for name in customization.vehicle_names():
            customization.delete_vehicle(name)
        customization.save_vehicle("QA-车辆", cells, cells)
        customization.select_vehicle("QA-车辆")

        # ---- 原样复刻 selftest.main() 的清零动作 ----
        for key in ("module_count", "cells_per_module", "temps_per_module"):
            settings.setValue(f"custom/{key}", 0)
        settings.setValue("custom/vehicle", "")
        settings.sync()

        print("=== 复刻 selftest.main() 的清零之后 ===")
        print("  三个旧键 =", [settings.value(f"custom/{k}")
                              for k in ("module_count", "cells_per_module",
                                        "temps_per_module")])
        print("  custom/vehicle =", repr(settings.value("custom/vehicle")))
        print("  current_vehicle() =", repr(customization.current_vehicle()))
        print("  active_profile()  =", customization.active_profile())
        still = customization.active_profile() is not None
        print("  → 当前车辆", "**仍然生效**（清零无效）" if still else "已清空")
        if still:
            failures.append("selftest 清空当前车辆无效（active_profile 仍非 None）")

        # ---- 实际跑三条敏感用例 ----
        for name, func in selftest.CHECKS:
            if name not in CHECKS:
                continue
            try:
                func(app) if func.__code__.co_argcount else func()
            except Exception as exc:                       # noqa: BLE001
                print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
                failures.append(f"{name} 在选中车辆时假挂：{type(exc).__name__}: {exc}")
            else:
                print(f"  PASS  {name}")
    finally:
        for widget in QApplication.topLevelWidgets():
            try:
                widget.close()
            except RuntimeError:
                pass
        pump(app, 200)
        selftest.restore_settings(settings, snapshot)
        settings.sync()

    print("\n================ QA 判定 ================")
    if failures:
        for item in failures:
            print("FAIL  " + item)
        return 1
    print("PASS  清零生效、三条敏感用例不受车辆影响")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
