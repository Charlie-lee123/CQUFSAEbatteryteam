# -*- coding: utf-8 -*-
r"""QA 找茬探针：车辆功能的健壮性（性能 / 坏 JSON / 异常车名）。

三组：
  A. 性能：active_profile() 被"每帧排版"调用 —— 量 10000 次耗时（没选车 /
     3 模组 / 64 模组 三种规模）；
  B. 坏 JSON：把 custom/vehicles 手工塞成各种非法/畸形值，看 API 会不会抛、
     设置面板还能不能建起来；
  C. 异常车名：引号 / 换行 / 超长名 —— JSON 是否仍合法、能否原样读回、
     设置面板会不会被撑出横向滚动。

用法：QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_qa_robustness.py
"""
from __future__ import annotations

import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings                              # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea           # noqa: E402

FAILS: list[str] = []


def pump(app, ms: int) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.003)


def bench(fn, n: int = 10000) -> float:
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) * 1e6 / n        # 每次调用 µs


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, ui_font
    apply_scheme("dark")
    app.setFont(ui_font(12.5))

    import customization
    from selftest import restore_settings

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    dialog = None
    try:
        # ================= A. 性能 =================
        for name in customization.vehicle_names():
            customization.delete_vehicle(name)
        print("=== A. active_profile() 每次调用耗时（10000 次）===")
        customization.select_vehicle("")
        cost_none = bench(customization.active_profile)
        print(f"  没选车             {cost_none:8.1f} µs/次")
        customization.save_vehicle("三模组", [25, 24, 25], [8, 5, 6])
        customization.select_vehicle("三模组")
        cost_3 = bench(customization.active_profile)
        print(f"  3 模组             {cost_3:8.1f} µs/次")
        customization.save_vehicle("16模组", [25] * 16, [8] * 16)
        customization.select_vehicle("16模组")
        cost_16 = bench(customization.active_profile)
        print(f"  16 模组            {cost_16:8.1f} µs/次")
        customization.save_vehicle("64模组", [25] * 64, [8] * 64)
        customization.select_vehicle("64模组")
        cost_64 = bench(customization.active_profile)
        print(f"  64 模组（上限）    {cost_64:8.1f} µs/次")
        print(f"  参考：一帧 30ms 合并窗口 → 64 模组也只用掉 "
              f"{cost_64 / 30000 * 100:.3f}% 的时间片")
        if cost_64 > 1000:
            FAILS.append(f"64 模组 active_profile 单次 {cost_64:.0f}µs 偏高")

        # ================= B. 坏 JSON =================
        print("\n=== B. custom/vehicles 被写坏时 ===")
        bad_cases = [
            ("完全不是 JSON", "not json at all"),
            ("JSON 是数组", "[1,2,3]"),
            ("JSON 是字符串", '"hello"'),
            ("JSON 是 null", "null"),
            ("items 不是列表", '{"current":"A","items":5}'),
            ("items 里混垃圾", '{"current":"A","items":[1,"x",null,{}]}'),
            ("cells/temps 非数字", '{"current":"A","items":[{"name":"A",'
                                   '"cells":["x",null,-5,99],"temps":"no"}]}'),
            ("current 指向不存在的车", '{"current":"幽灵","items":[{"name":"A",'
                                      '"cells":[25],"temps":[8]}]}'),
            ("重复名字", '{"current":"A","items":[{"name":"A","cells":[25],'
                         '"temps":[8]},{"name":"A","cells":[1],"temps":[1]}]}'),
            ("超长垃圾 1MB", "{" + "x" * (1 << 20)),
        ]
        for label, raw in bad_cases:
            t0 = time.perf_counter()
            settings.setValue("custom/vehicles", raw)
            settings.sync()
            try:
                names = customization.vehicle_names()
                current = customization.current_vehicle()
                profile = customization.active_profile()
                elapsed = (time.perf_counter() - t0) * 1000
                print(f"  {label:<18} → names={names} current={current!r} "
                      f"profile={profile} ({elapsed:.0f}ms)")
            except Exception as exc:                       # noqa: BLE001
                FAILS.append(f"坏 JSON「{label}」抛异常：{type(exc).__name__}: {exc}")
                print(f"  {label:<18} → 抛异常 {type(exc).__name__}: {exc}")
        # 坏 JSON 下设置面板还能不能建
        settings.setValue("custom/vehicles", "{坏掉的")
        settings.sync()
        try:
            from ui_settings import SettingsDialog
            dialog = SettingsDialog("dark", 55)
            dialog.show()
            pump(app, 300)
            print(f"  坏 JSON 下建设置面板 → 下拉={dialog.cmb_vehicle.currentText()!r} "
                  f"逐模组行={len(dialog._module_rows)} 旧三件套可见="
                  f"{[dialog._custom_rows[k][1].isVisibleTo(dialog) for k in dialog._LEGACY_GRID_KEYS]}")
            dialog.close()
            dialog.deleteLater()
            pump(app, 200)
            dialog = None
        except Exception as exc:                           # noqa: BLE001
            FAILS.append(f"坏 JSON 下设置面板建不起来：{type(exc).__name__}: {exc}")

        # ================= C. 异常车名 =================
        print("\n=== C. 异常车名 ===")
        settings.setValue("custom/vehicles", "")
        settings.sync()
        for name in customization.vehicle_names():
            customization.delete_vehicle(name)
        weird = {
            "双引号": '车"名"带引号',
            "反斜杠": r"车\名\\带转义",
            "换行": "第一行\n第二行",
            "制表符": "车\t名",
            "超长": "超" * 300,
            "表情/生僻": "车😀𠮷",
            "只有空格": "   ",
        }
        from ui_settings import SettingsDialog
        for label, name in weird.items():
            customization.save_vehicle(name, [25, 24, 25], [8, 5, 6])
            stored = [v["name"] for v in customization.vehicles()]
            raw = settings.value("custom/vehicles")
            try:
                parsed = json.loads(raw)
                json_ok = parsed.get("current") is not None
            except (TypeError, ValueError) as exc:
                json_ok = False
                print(f"  {label:<8} JSON 解析失败: {exc}")
            print(f"  {label:<8} 存入={stored!r} JSON合法={json_ok}")
            if not json_ok:
                FAILS.append(f"车名「{label}」把 custom/vehicles 写成了非法 JSON")
            if name.strip() and name.strip() not in [s.strip() for s in stored]:
                FAILS.append(f"车名「{label}」没存进去（被过滤或截断）")
            customization.delete_vehicle(name)

        # 超长名在面板里会不会撑出横向滚动
        long_name = "超长车辆名称" * 40
        customization.save_vehicle(long_name, [25, 24, 25], [8, 5, 6])
        customization.select_vehicle(long_name)
        dialog = SettingsDialog("dark", 55)
        dialog.resize(dialog.minimumWidth(), 880)
        dialog.show()
        pump(app, 500)
        scroll = dialog.findChild(QScrollArea)
        body, viewport = scroll.widget(), scroll.viewport()
        hbar = scroll.horizontalScrollBar()
        print(f"\n  超长名（{len(long_name)} 字）面板最小宽 {dialog.minimumWidth()}："
              f"body {body.width()} / 视口 {viewport.width()} / "
              f"body 最小宽 {body.minimumSizeHint().width()} / "
              f"横条 {hbar.minimum()}..{hbar.maximum()}")
        if body.width() != viewport.width() or hbar.maximum() != hbar.minimum():
            FAILS.append(f"超长车名撑出横向溢出：body {body.width()} vs 视口 "
                         f"{viewport.width()}，横条 {hbar.minimum()}..{hbar.maximum()}")
        dialog.close()
        dialog.deleteLater()
        pump(app, 200)
        dialog = None
    finally:
        if dialog is not None:
            try:
                dialog.close()
            except RuntimeError:
                pass
        pump(app, 150)
        restore_settings(settings, snapshot)
        settings.sync()

    print("\n================ QA 判定 ================")
    if FAILS:
        for item in FAILS:
            print("FAIL  " + item)
        return 1
    print("PASS  性能可接受、坏 JSON 不崩、异常车名安全")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
