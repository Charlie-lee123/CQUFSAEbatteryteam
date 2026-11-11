# -*- coding: utf-8 -*-
r"""电压 4 位左零补齐验收探针（独立离线，不改产品源码）。

覆盖用户新需求「电压数据都要是 4 位的，不足的请在前面加零补齐」在**两处 CSV 导出**
的落地：

  A  rx_export.Session.write_csv —— 电压行用 ``f"{v:04d}"``，温度行保持原样；
  B  rx_store._join_values —— 新增 ``width`` 参数，电压 cells 传 width=4、温度不补。

判据（来自 team-lead 派单）：
  - rx_export: configure([3],[0]) → feed("Voltage590: 01.0050 02.0005 03.3851")
              → write_csv → 读回模组行电压列 == ["0050","0005","3851"]
  - rx_store : _join_values("[50,5,3851]", 4) == "0050 0005 3851"
               _join_values("[25,5]")       == "25 5"  （不补、坏 JSON 原样回）

用法：.venv/Scripts/python.exe devtools\_verify_voltage_padding.py
产物：_check/voltage_padding.txt
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import rx_export                                                       # noqa: E402
import rx_store                                                        # noqa: E402

OUT = os.path.join(_ROOT, "_check")
os.makedirs(OUT, exist_ok=True)
_RESULTS: list[tuple[str, bool, str]] = []
_LINES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append((name, bool(ok), detail))
    line = f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else "")
    print(line)
    _LINES.append(line)
    return ok


def read_csv(path: str) -> list[list[str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


def case_rx_export_csv() -> None:
    """A：宽度表导出 —— 电压 4 位补齐、温度不补。"""
    sess = rx_export.Session()
    sess.configure([3], [0])
    sess.feed("Voltage590: 01.0050 02.0005 03.3851\n")

    tmp = os.path.join(tempfile.gettempdir(), "vpad_rx_export.csv")
    rows = sess.write_csv(tmp)
    table = read_csv(tmp)
    head = table[0]
    check("A0 CSV 落盘且有表头+1 行模组",
          rows == 1 and len(table) == 2, f"write_csv={rows} 文件行数={len(table)}")

    # 表头：时间 / 轮次 / 模组 / 电芯1..3(mV)；温度列因配置 [0] 不出现
    i_c1 = head.index("电芯1(mV)")
    got = table[1][i_c1:i_c1 + 3]
    exp = ["0050", "0005", "3851"]
    check("A1 电压列 == [\u201c0050\u201d, \u201c0005\u201d, \u201c3851\u201d]（4 位左零补齐）",
          got == exp, f"实得 {got} 期望 {exp}")

    # 反向：高电压不截断、不补多余零
    sess2 = rx_export.Session()
    sess2.configure([2], [0])
    sess2.feed("Voltage1: 01.3851 02.3850\n")
    tmp2 = os.path.join(tempfile.gettempdir(), "vpad_rx_export2.csv")
    sess2.write_csv(tmp2)
    t2 = read_csv(tmp2)
    got2 = t2[1][t2[0].index("电芯1(mV)"):t2[0].index("电芯1(mV)") + 2]
    check("A2 高电压 3851/3850 不截断、不补多余零",
          got2 == ["3851", "3850"], f"实得 {got2}")

    # 温度行不变：configure([3],[2]) + 温度帧（原生 `序号.值` 口吻），温度列不得被补零
    sess3 = rx_export.Session()
    sess3.configure([3], [2])
    sess3.feed("Voltage590: 01.0050 02.0005 03.3851\n")
    sess3.feed("Temperature1: 01.05 02.07\n")
    tmp3 = os.path.join(tempfile.gettempdir(), "vpad_rx_export3.csv")
    sess3.write_csv(tmp3)
    t3 = read_csv(tmp3)
    ti = t3[0].index("温度1(℃)")
    temps = t3[1][ti:ti + 2]
    check("A3 温度列保持原样（5/7 不被补成 0005/0007 或 05/07）",
          temps == ["5", "7"], f"实得 {temps}")


def case_rx_store_join() -> None:
    """B：会话库 CSV 导出 —— _join_values(width=4) 对电压补齐。"""
    check('B1 _join_values("[50,5,3851]", 4) == "0050 0005 3851"',
          rx_store._join_values("[50,5,3851]", 4) == "0050 0005 3851",
          repr(rx_store._join_values("[50,5,3851]", 4)))
    check('B2 _join_values("[25,5]") == "25 5"（默认 width=0 不补）',
          rx_store._join_values("[25,5]") == "25 5",
          repr(rx_store._join_values("[25,5]")))

    # 边界：width=0 时坏 JSON 原样返回；width=4 时同样坏 JSON 原样返回
    check("B3 坏 JSON 原样返回（width=0 不炸）",
          rx_store._join_values("not-json") == "not-json",
          repr(rx_store._join_values("not-json")))
    check("B4 坏 JSON 原样返回（width=4 不炸）",
          rx_store._join_values("not-json", 4) == "not-json",
          repr(rx_store._join_values("not-json", 4)))

    # 单值 / 越界宽度语义：值超过 4 位（如 99999）只补零不截断
    check("B5 超过 4 位不截断（仅左零补齐）",
          rx_store._join_values("[99999]", 4) == "99999",
          repr(rx_store._join_values("[99999]", 4)))


def main() -> int:
    _LINES.append("=" * 92)
    _LINES.append("电压 4 位左零补齐验收（rx_export / rx_store CSV 导出）")
    _LINES.append("=" * 92)
    case_rx_export_csv()
    case_rx_store_join()

    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    tail = f"\n合计 {passed}/{total} PASS  ->  IS_PASS: {'YES' if passed == total else 'NO'}"
    print(tail)
    _LINES.append(tail)
    with open(os.path.join(OUT, "voltage_padding.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(_LINES) + "\n")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
