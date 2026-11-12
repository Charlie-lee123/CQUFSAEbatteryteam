"""功能验证脚本（2026-11-13）：电压/温度独立行数、显示补零开关。

不依赖 GUI，仅用 ``QApplication([])`` 起一个最小 Qt 环境（customization 的
``get`` 走 QSettings，必须有个 Qt 应用实例）。所有对 QSettings 的改动都在
「快照 → 改 → 还原」里进行，绝不污染用户真实配置（与 selftest.py 同一套口径）。

运行：
    cd /f/MimoCodeworks/SerialAssistant
    QT_QPA_PLATFORM=windows ./.venv/Scripts/python.exe devtools/_verify_feature_20261113.py
退出码 0 = 全部通过。
"""

import os
import sys

# 保证能从项目根 import
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import customization  # noqa: E402
from ui_highlight import (  # noqa: E402
    SECTION_VOLT, align_indexes,
)

ORG, APP = "MimoWorks", "SerialAssistant"


def snapshot() -> dict:
    s = QSettings(ORG, APP)
    return {k: s.value(k) for k in s.allKeys()}


def restore(snap: dict) -> None:
    s = QSettings(ORG, APP)
    for k in s.allKeys():
        if k not in snap:
            s.remove(k)
    for k, v in snap.items():
        if v is None:
            s.remove(k)
        else:
            s.setValue(k, v)
    s.sync()


def main() -> int:
    QApplication([])  # 最小 Qt 环境
    failures: list[str] = []

    # ---- 1. 三个新键已注册，默认值为 0 / 0 / True ----
    try:
        vr = customization.get("volt_rows")
        tr = customization.get("temp_rows")
        pz = customization.get("pad_zero")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"读取新键抛异常: {exc}")
        vr = tr = pz = None
    if vr != 0:
        failures.append(f"volt_rows 默认应为 0，实际 {vr!r}")
    if tr != 0:
        failures.append(f"temp_rows 默认应为 0，实际 {tr!r}")
    if pz is not True:
        failures.append(f"pad_zero 默认应为 True，实际 {pz!r}")

    # ---- 2. 补零开关：False 时把 0 换成同宽空格，总长度一致 ----
    snap = snapshot()
    try:
        s = QSettings(ORG, APP)
        # 一段电压数据：23.402 位数不足（只在电压段被当 mV 判），需要补到 4 位
        data = "01.3820 23.402"

        s.setValue("custom/pad_zero", True)
        s.sync()
        out_zero = align_indexes(data, section=SECTION_VOLT)
        s.setValue("custom/pad_zero", False)
        s.sync()
        out_space = align_indexes(data, section=SECTION_VOLT)

        # pad_zero=True：值被补成 "0402"
        if "23.0402" not in out_zero:
            failures.append(f"pad_zero=True 应补成 23.0402，实际输出: {out_zero!r}")
        # pad_zero=False：补成空格 " 402"，且不含 "0402"
        if "23. 402" not in out_space:
            failures.append(f"pad_zero=False 应补成空格 23. 402，实际输出: {out_space!r}")
        if "0402" in out_space:
            failures.append(f"pad_zero=False 不应出现前导 0（0402），实际: {out_space!r}")
        # 总长度一致（等宽字体下视觉对齐）
        if len(out_zero) != len(out_space):
            failures.append(
                f"两种模式长度应一致: True={len(out_zero)} False={len(out_space)}")
    finally:
        restore(snap)

    # ---- 3. 电压区独立行数 volt_rows>0：行数≈该值 ----
    snap = snapshot()
    try:
        s = QSettings(ORG, APP)
        # 10 个电压点，volt_rows=3 → 应排成 3 行
        data = " ".join(f"{i:02d}.3820" for i in range(1, 11))
        s.setValue("custom/volt_rows", 3)
        s.sync()
        out = align_indexes(data, section=SECTION_VOLT, volt_rows_hint=3)
        row_count = out.count("\n") + 1
        if row_count != 3:
            failures.append(f"volt_rows=3 应排成 3 行，实际 {row_count} 行: {out!r}")

        # volt_rows=0（自动）：默认行为不变，10 个点且 cols_hint=0 → 1 行
        s.setValue("custom/volt_rows", 0)
        s.sync()
        out_auto = align_indexes(data, section=SECTION_VOLT)
        # 自动分支：没给 cols_hint，10 个点一行出一列 → 1 行
        if out_auto.count("\n") + 1 != 1:
            failures.append(
                f"volt_rows=0 自动应 1 行，实际 {out_auto.count(chr(10)) + 1} 行: "
                f"{out_auto!r}")
    finally:
        restore(snap)

    if failures:
        print("FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS: 三个功能键已注册、显示补零开关两态长度一致、电压区独立行数生效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
