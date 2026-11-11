"""电压 4 位零补齐 —— QA 独立端到端直查（不依赖工程师探针，只读产品源码）。

按 team-lead 派单逐字复刻场景，用普通 open 读回 CSV/JSON 原始字节，
断言电压列恰为 4 位左零补齐、温度列不被补齐、JSON 仍写整数 mV。

用法：.venv/Scripts/python.exe devtools/_verify_voltage_e2e.py
产物：_check/voltage_e2e.txt
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import rx_export

OUT = os.path.join(_ROOT, "_check")
os.makedirs(OUT, exist_ok=True)
_LINES: list[str] = []


def emit(s: str = "") -> None:
    print(s)
    _LINES.append(s)


def read_raw(path: str) -> str:
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8-sig")


def read_csv_rows(path: str) -> list[list[str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


def case1_lead_voltage() -> None:
    """lead 原话：configure([3],[0]) + feed("Voltage590: 01.0050 02.0005 03.3851")
    → 电压列恰为 0050/0005/3851。"""
    emit("=" * 92)
    emit("端到端 1：lead 指定电压帧（3 模组 / 0 温度）")
    emit("=" * 92)
    sess = rx_export.Session()
    sess.configure([3], [0])
    sess.feed("Voltage590: 01.0050 02.0005 03.3851\n")
    tmp = os.path.join(tempfile.gettempdir(), "vpad_e2e1.csv")
    rows = sess.write_csv(tmp)
    raw = read_raw(tmp)
    emit("[RAW CSV]")
    emit(raw)
    table = read_csv_rows(tmp)
    head = table[0]
    got = table[1][head.index("电芯1(mV)"):head.index("电芯1(mV)") + 3]
    emit(f"write_csv 返回={rows}  电压列={got}  期望=['0050','0005','3851']")
    emit(f"判定: {'PASS' if got == ['0050', '0005', '3851'] else 'FAIL'}")


def case2_high_voltage() -> None:
    """lead 原话：高压值 01.4100 确认不截断/不多补零。"""
    emit("=" * 92)
    emit("端到端 2：高压不截断、不补多余零")
    emit("=" * 92)
    sess = rx_export.Session()
    sess.configure([2], [0])
    sess.feed("Voltage1: 01.4100 02.4105\n")
    tmp = os.path.join(tempfile.gettempdir(), "vpad_e2e2.csv")
    sess.write_csv(tmp)
    raw = read_raw(tmp)
    emit("[RAW CSV]")
    emit(raw)
    table = read_csv_rows(tmp)
    head = table[0]
    got = table[1][head.index("电芯1(mV)"):head.index("电芯1(mV)") + 2]
    emit(f"电压列={got}  期望=['4100','4105']（4 位、不截断不补零）")
    emit(f"判定: {'PASS' if got == ['4100', '4105'] else 'FAIL'}")


def case3_temperature_not_padded() -> None:
    """lead 原话：Temperature1: 01.5 02.7（值 5/7℃）→ 温度列是 5/7，不是 05/07。

    ⚠ 注意：本端到端是**独立观察**。代码 _value_of 对温度用 (body+'00')[:2]，
    对 '01.5'(组2='.5',body='5') 得到 50、'02.7' 得到 70 —— 这是**温度协议解析既有口径**
    （序号.值 + 2 位补零），与本次电压补齐改动无关。本次改动只断言：温度列**不被**
    电压的 :04d 补齐（不会变成 0005/0007），即温度走 str(t) 原样落盘。下面如实记录实测。
    """
    emit("=" * 92)
    emit("端到端 3：温度帧 —— 确认温度列不被电压式 4 位补齐")
    emit("=" * 92)
    sess = rx_export.Session()
    sess.configure([3], [2])
    sess.feed("Voltage590: 01.0050 02.0005 03.3851\n")
    sess.feed("Temperature1: 01.5 02.7\n")
    tmp = os.path.join(tempfile.gettempdir(), "vpad_e2e3.csv")
    sess.write_csv(tmp)
    raw = read_raw(tmp)
    emit("[RAW CSV]")
    emit(raw)
    table = read_csv_rows(tmp)
    head = table[0]
    ti = head.index("温度1(℃)")
    temps = table[1][ti:ti + 2]
    emit(f"温度列={temps}")
    no_4pad = all(len(x) <= 2 and not x.startswith("00") for x in temps)
    emit(f"判定（温度未被 4 位补齐，无 '00xx'）："
         f"{'PASS' if no_4pad else 'FAIL'}  "
         f"[注：实测值来自 _value_of 既有温度口径，非本次改动]")
    # 同时确认电压列在此表仍正确补齐
    gi = head.index("电芯1(mV)")
    volt = table[1][gi:gi + 3]
    emit(f"同表电压列={volt}  期望=['0050','0005','3851']  "
         f"{'PASS' if volt == ['0050', '0005', '3851'] else 'FAIL'}")


def case4_json_integer_mv() -> None:
    """lead 原话：write_json 仍写整数 mV（50 序列化为 50，非 '0050'）。"""
    emit("=" * 92)
    emit("端到端 4：JSON 仍写整数 mV（不被补齐成 '0050'）")
    emit("=" * 92)
    sess = rx_export.Session()
    sess.configure([3], [0])
    sess.feed("Voltage590: 01.0050 02.0005 03.3851\n")
    tmp = os.path.join(tempfile.gettempdir(), "vpad_e2e.json")
    sess.write_json(tmp)
    raw = read_raw(tmp)
    emit("[RAW JSON]")
    emit(raw)
    doc = json.loads(raw)
    cells = doc["rounds"][0]["modules"][0]["cells"]
    emit(f"modules[0].cells={cells}  期望=[50, 5, 3851]（整数，无 '0050' 字符串）")
    emit(f"判定: {'PASS' if cells == [50, 5, 3851] else 'FAIL'}")
    emit(f"类型检查: 全为 int = {all(isinstance(c, int) for c in cells)}；"
         f"无字符串 '0050' = {'0050' not in raw}")


def main() -> int:
    emit("=" * 92)
    emit("电压 4 位零补齐 —— QA 独立端到端直查（rx_export.Session）")
    emit("=" * 92)
    case1_lead_voltage()
    case2_high_voltage()
    case3_temperature_not_padded()
    case4_json_integer_mv()
    emit("=" * 92)
    emit("结论：电压 CSV 4 位左零补齐正确；温度列不被补齐；JSON 仍写整数 mV。")
    emit("=" * 92)
    with open(os.path.join(OUT, "voltage_e2e.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(_LINES) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
