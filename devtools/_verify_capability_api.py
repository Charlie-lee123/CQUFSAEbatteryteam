# -*- coding: utf-8 -*-
"""``capability_api`` 的只读契约验证（不写用户目录 / 不启动主窗 / 不跑 selftest）。

验证项
------
1. ``list_tools()`` 非空、每项字段齐全、``mutating`` 全为 False；
2. 代表性 P0 调用全部 ``ok=True`` 且 ``json.dumps(data)`` 成功；
3. 62 点 OCV 表端点数值回归；
4. 异常隔离：垃圾参数一律 ``ok=False`` 而不是抛异常；
5. P1 只读能力冒烟（顺带用「读 QSettings」把零污染判定喂实）；
6. **QSettings 零污染**：进程序全量快照（含所有 ``allKeys()``），
   ``finally`` 里无条件还原（新增键删掉），断言前后完全一致。
   ⛔ 干净路径对真实配置零 ``setValue/sync/remove`` 调用。

⚠️ 不 import QtWidgets、不构造 MainWindow、不跑 selftest、不写用户目录。
   用项目 ``.venv/Scripts/python.exe`` 跑（P1 需要 PySide6.QtCore）。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings  # noqa: E402  ⛔ 只用 QtCore，绝不碰 QtWidgets

import capability_api as C  # noqa: E402

_ORG, _APP = "MimoWorks", "SerialAssistant"


def _snapshot(settings):
    """QSettings 全量快照：键 → 值（含所有 allKeys()）。"""
    return {k: settings.value(k) for k in settings.allKeys()}


def _json_ok(data):
    """严格 JSON 可序列化（allow_nan=False，NaN/Inf 视为失败）。"""
    try:
        json.dumps(data, ensure_ascii=False, allow_nan=False)
        return True
    except (TypeError, ValueError):
        return False


def main() -> int:
    settings = QSettings(_ORG, _APP)
    before = _snapshot(settings)
    fails: list[str] = []

    def check(cond, label, detail=""):
        if cond:
            print(f"  PASS  {label}" + (f"    {detail}" if detail else ""))
        else:
            fails.append(label)
            print(f"  FAIL  {label}    {detail}")

    try:
        # ---- 1. list_tools 契约 ------------------------------------------
        tools = C.list_tools()
        check(len(tools) > 0, "list_tools() 返回非空", f"共 {len(tools)} 项能力")
        check(all(set(t) == {"name", "description", "params", "mutating", "requires"}
                  for t in tools),
              "每项字段齐全（name/description/params/mutating/requires）")
        check(all(t["mutating"] is False for t in tools),
              "mutating 全为 False（本层不暴露写能力）")
        p0 = [t for t in tools if t["requires"] is None]
        p1 = [t for t in tools if t["requires"] == "PySide6"]
        print(f"        分层：零 Qt P0={len(p0)} 项 / PySide6 P1={len(p1)} 项")

        # ---- 2. 代表性 P0 调用 -------------------------------------------
        p0_calls = [
            ("ocv_from_soc", {"soc": 50}),
            ("soc_from_ocv", {"ocv_v": 3.9}),
            ("clean_cells", {"cells_mv": [3900, 3910, 3895, 3905, 0, None, 3880],
                             "n_cells": 7, "cells_per_module": 4}),
            ("parse_text", {"text": "Voltage1:\n01.3900 02.3910 03.3895\n"
                                    "CVMax=4000, Min=3000"}),
            ("soc_ekf4_step", {"v_cell_v": 3.9, "i_a": 0.0, "dt_s": 1.0}),
            ("rx_export_session", {"text": "Voltage1: 01.3900 02.3910\nBMS OK",
                                   "stamp": "12:00:00.000"}),
        ]
        for name, params in p0_calls:
            r = C.call(name, params)
            check(r["ok"] and _json_ok(r["data"]),
                  f"P0 {name} ok=True 且 json.dumps(data) 成功",
                  "" if r["ok"] else str(r["error"]))

        # SocEstimator.update → 其返回 dict 直接喂给 format_soc_parts（可组合性）
        upd = C.call("soc_estimator_update",
                     {"cells_mv": [3900.0] * 125, "i_meas": 0.0, "ts": 0.0})
        check(upd["ok"] and _json_ok(upd["data"]),
              "P0 SocEstimator.update ok=True 且 json.dumps(data) 成功",
              "" if upd["ok"] else str(upd["error"]))
        fmt = C.call("format_soc_parts", {"res": upd["data"]})
        check(fmt["ok"] and _json_ok(fmt["data"]),
              "P0 format_soc_parts ok=True 且 json.dumps(data) 成功",
              "" if fmt["ok"] else str(fmt["error"]))

        # ---- 3. 数值回归（当前 62 点 OCV 表端点）-------------------------
        regressions = [
            ("ocv_from_soc(0)", "ocv_from_soc", {"soc": 0}, 3.0818, 1e-4),
            ("ocv_from_soc(100)", "ocv_from_soc", {"soc": 100}, 4.4981, 1e-4),
            ("soc_from_ocv(3.0818)", "soc_from_ocv", {"ocv_v": 3.0818}, 0.0, 1e-4),
            ("soc_from_ocv(4.4981)", "soc_from_ocv", {"ocv_v": 4.4981}, 100.0, 1e-4),
        ]
        for label, name, params, expect, tol in regressions:
            r = C.call(name, params)
            got = r["data"] if isinstance(r["data"], (int, float)) else None
            check(r["ok"] and got is not None and abs(got - expect) < tol,
                  f"数值回归 {label}", f"got={got} expect≈{expect}")

        # ---- 4. 异常隔离（垃圾参数必须 ok=False，不得抛异常）-------------
        garbage = [
            ("未知 tool 名", "__no_such_tool__", {}),
            ("超长字符串", "parse_text", {"text": "x" * 300000}),
            ("cells_mv 塞 NaN", "clean_cells", {"cells_mv": [3900, float("nan"), 3910]}),
            ("cells_mv 塞字符串", "clean_cells", {"cells_mv": [3900, "abc", 3910]}),
            ("负数 soc", "ocv_from_soc", {"soc": -5}),
            ("Inf soc", "ocv_from_soc", {"soc": float("inf")}),
            ("目录穿越", "rx_history_labels", {"folder": "../../etc"}),
        ]
        for label, name, params in garbage:
            try:
                r = C.call(name, params)
                isolated = (r["ok"] is False and r["data"] is None
                            and isinstance(r["error"], dict)
                            and bool(r["error"].get("type")))
                detail = "" if isolated else str(r)
            except Exception as exc:                        # noqa: BLE001
                isolated = False
                detail = f"抛异常 {type(exc).__name__}: {exc}"
            check(isolated, f"异常隔离 {label} → ok=False", detail)

        # ---- 5. P1 只读冒烟（读 QSettings，喂实第 6 步零污染判定）--------
        # ⚠️ 刻意不调 dlc_discover：discover() 内部 user_dir() 会 mkdir，
        #    为满足「不写用户目录」本脚本不碰它（已用 CLI 单独验证）。
        p1_smoke = [
            ("builtin_packs", {}),
            ("cfg_for", {"vehicle": "E74-2026"}),
            ("customization_vehicles", {}),
            ("customization_vehicle_names", {}),
            ("customization_current_vehicle", {}),
            ("customization_active_profile", {}),
            ("customization_get", {"key": "startup_maximized"}),
            ("customization_specs", {}),
            ("rx_store_info", {}),
            ("rx_history_labels", {"folder": "logs"}),
        ]
        for name, params in p1_smoke:
            r = C.call(name, params)
            if r["ok"]:
                check(_json_ok(r["data"]),
                      f"P1 {name} 只读调用 ok=True 且可序列化")
            elif r["error"] and r["error"]["type"] == "Unavailable":
                print(f"  SKIP  P1 {name}（无 PySide6，优雅降级 Unavailable，符合契约）")
            else:
                check(False, f"P1 {name} 只读调用", str(r["error"]))
        print("        注：dlc_discover 已暴露但本脚本不调用（避免 discover() 内部 mkdir）")

        # ---- 6. QSettings 零污染 -----------------------------------------
        after = _snapshot(settings)
        check(after == before,
              "QSettings 全量快照前后完全一致（零污染）",
              f"快照 {len(before)} 键 / 收尾 {len(after)} 键")
        print(f"        零污染证据：before={len(before)} 键，after={len(after)} 键，"
              f"新增键={sorted(set(after) - set(before))}，"
              f"丢失键={sorted(set(before) - set(after))}")
    finally:
        # 无条件还原防御网：finally 恒定执行；但**只在检测到漂移时才真正写**，
        # 干净路径对真实配置零 setValue / sync / remove 调用。
        if _snapshot(settings) != before:
            for key in settings.allKeys():
                if key not in before:
                    settings.remove(key)
            for key, value in before.items():
                if settings.value(key) != value:
                    settings.setValue(key, value)
            settings.sync()
            print("        （检测到漂移，已在 finally 无条件还原）")

    if fails:
        print(f"\n失败 {len(fails)} 项：")
        for label in fails:
            print(f"  - {label}")
        print("IS_PASS: NO")
        return 1
    print("\nIS_PASS: YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
