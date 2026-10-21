# -*- coding: utf-8 -*-
r"""QA 独立探针：车辆档案的**跨进程持久化**（等价于"关程序再打开"）。

三档用法（各跑一次独立进程 —— 只有进程真的退出，QSettings/注册表才落盘）：

    python devtools/_qa_persist.py save            # 模拟用户在程序里建车并选中
    python devtools/_qa_persist.py read            # 模拟重开程序，读回
    python devtools/_qa_persist.py restore <json>  # 还原用户真实配置
    python devtools/_qa_persist.py snapshot <json> # 备份用户真实配置

⛔ 只碰 custom/vehicles 与 custom/vehicle 两个键；restore 会把它们原样还回去。
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QSettings                                # noqa: E402

ORG, APP = "MimoWorks", "SerialAssistant"
KEYS = ("custom/vehicles", "custom/vehicle")


def _dump(path: str) -> None:
    settings = QSettings(ORG, APP)
    data = {key: settings.value(key) for key in KEYS}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    print("已备份：", {k: (v if v is None else "<有值>") for k, v in data.items()})


def _restore(path: str) -> None:
    settings = QSettings(ORG, APP)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    for key in KEYS:
        value = data.get(key)
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)
    settings.sync()
    print("已还原：", {k: settings.value(k) for k in KEYS})


def main(argv: list[str]) -> int:
    import customization

    mode = argv[1] if len(argv) > 1 else "read"
    if mode == "snapshot":
        _dump(argv[2])
        return 0
    if mode == "restore":
        _restore(argv[2])
        return 0

    if mode == "save":
        for name in customization.vehicle_names():
            customization.delete_vehicle(name)
        customization.save_vehicle("持久化验证车", [25, 24, 25], [8, 6, 5])
        customization.save_vehicle("另一台", [10, 12], [4, 4])
        customization.select_vehicle("持久化验证车")
        print("进程 A 已写入：", customization.vehicle_names(),
              "当前 =", customization.current_vehicle())
        return 0

    # read：全新进程，什么都不要预先建
    names = customization.vehicle_names()
    current = customization.current_vehicle()
    profile = customization.active_profile()
    settings = QSettings(ORG, APP)
    raw = settings.value("custom/vehicles")
    mirror = settings.value("custom/vehicle")
    print("进程 B 读回：")
    print("  vehicle_names()   =", names)
    print("  current_vehicle() =", current)
    print("  active_profile()  =", profile)
    print("  custom/vehicle    =", repr(mirror))
    print("  custom/vehicles   =", raw)
    ok = True
    try:
        parsed = json.loads(raw or "{}")
        print("  JSON 合法 ✓ keys =", sorted(parsed), "items =",
              [(i.get("name"), i.get("cells"), i.get("temps"))
               for i in parsed.get("items", [])])
    except (TypeError, ValueError) as exc:
        print("  JSON 非法 ✗", exc)
        ok = False
    if names != ["持久化验证车", "另一台"]:
        print("  ✗ 车辆列表没保住")
        ok = False
    if current != "持久化验证车":
        print("  ✗ 当前选择没保住")
        ok = False
    if profile != ([25, 24, 25], [8, 6, 5]):
        print("  ✗ 结构没保住")
        ok = False
    if mirror != current:
        print("  ✗ 两个键不一致（镜像键与 JSON.current 各说各话）")
        ok = False
    print("  →", "PASS 跨进程持久化成立" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
