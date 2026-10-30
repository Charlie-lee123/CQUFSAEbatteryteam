# -*- coding: utf-8 -*-
r"""v2.5 验收探针 A：结构化导出（CSV/JSON）—— **证伪向**，不改产品源码。

独立造数（不用实施者的 selftest 样例），逐项断言：
  A  数值正确（mV 整数 / 温度数值 / 负温 / 0）
  B  分模组不失位（模组 2/3 的第一个电芯落在「电芯1」列）
  C  分包帧（每帧都带 Voltage1:）按同一轮合并
  D  异常输入（\r、行首缩进、标题后空格、数字间多空格、缺 BMS）
  E  残缺模组（配置 25 芯只来 3 个值 + BMS）不能丢
  F  文件格式（UTF-8 BOM / 逗号 / 列数与表头一致 / JSON 可 json.load）
  G  只留最近 500 轮
  H  **真机格式**（设备发的是 `序号.mV`，如 `01.3851`）—— 与显示层同口径核对

用法：.venv/Scripts/python.exe devtools\_verify25_export.py
产物：_check/verify25_export.txt
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
from decimal import Decimal, ROUND_HALF_UP

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import rx_export                                                    # noqa: E402

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


def info(name: str, detail: str) -> None:
    line = f"INFO  {name}   [{detail}]"
    print(line)
    _LINES.append(line)


def mv(value: str) -> int:
    """把 '3.8712' 按项目口径换成 mV 整数（四舍五入，与 int(round(v*1000)) 比对）。"""
    return int((Decimal(value) * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def read_csv(path: str) -> list[list[str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


# ------------------------------------------------------------------ A/B/C/F
def case_basic() -> None:
    """3 模组 × 6 芯、温度 3 项/模组；值带小数尾巴、含 0 与负温。"""
    sess = rx_export.Session()
    sess.configure([6, 6, 6], [3, 3, 3])
    volt = ("3.8712 3.8001 3.7999 3.8000 3.7502 3.7000 "
            "4.1234 4.0000 0.0000 3.6009 3.5998 3.5001 "
            "3.9000 3.9001 3.9000 3.8999 3.8800 3.8700")
    sess.feed("Voltage1:\n" + " ".join(volt.split()[:6]))
    sess.feed("Voltage1:\n" + " ".join(volt.split()[6:12]))      # 分包（每帧带标题）
    sess.feed("Voltage1:\n" + " ".join(volt.split()[12:]))
    sess.feed("Temperature1:\n25.5 26.0 -5.5\n24.0 25.05 26.5\n-1.0 0.0 30.2")
    sess.feed("BMS OK")

    rounds = sess.rounds()
    check("A1 一轮 = 一个 BMS 周期", len(rounds) == 1, f"{len(rounds)} 轮")
    if not rounds:
        return
    rnd = rounds[0]
    check("A2 模组数 = 3", len(rnd["volt"]) == 3, f"{len(rnd['volt'])} 模组")
    check("A3 每模组 6 个电芯",
          [len(m) for m in rnd["volt"]] == [6, 6, 6], f"{[len(m) for m in rnd['volt']]}")
    check("A4 每模组 3 个温度",
          [len(m) for m in rnd["temp"]] == [3, 3, 3], f"{[len(m) for m in rnd['temp']]}")

    csv_path = os.path.join(OUT, "v25_export_basic.csv")
    rows = sess.write_csv(csv_path)
    table = read_csv(csv_path)
    head = table[0]
    info("A0 CSV 表头", ",".join(head))
    check("A5 CSV 行数 = 3（一行一个模组）", rows == 3 and len(table) == 4,
          f"write_csv 返回 {rows}，文件 {len(table) - 1} 行")
    i_c1 = head.index("电芯1(mV)")
    t_c1 = head.index("温度1(℃)")

    want = [
        ["3.8712", "3.8001", "3.7999", "3.8000", "3.7502", "3.7000"],
        ["4.1234", "4.0000", "0.0000", "3.6009", "3.5998", "3.5001"],
        ["3.9000", "3.9001", "3.9000", "3.8999", "3.8800", "3.8700"],
    ]
    for mod, tokens in enumerate(want):
        got = table[1 + mod][i_c1:i_c1 + 6]
        exp = [str(mv(t)) for t in tokens]
        check(f"B{mod + 1} 模组{mod + 1} 电芯列=mV 整数且落在电芯1..6",
              got == exp, f"实得 {got} 期望 {exp}")

    check("A6 模组号列 = 1/2/3",
          [r[2] for r in table[1:]] == ["1", "2", "3"],
          f"{[r[2] for r in table[1:]]}")
    check("A7 同一轮各模组共用时间戳",
          len({r[0] for r in table[1:]}) == 1 and table[1][0] != "",
          f"{[r[0] for r in table[1:]]}")
    check("A8 轮次列全为 1", {r[1] for r in table[1:]} == {"1"},
          f"{[r[1] for r in table[1:]]}")

    # 温度：至少要把 -5.5 / 0 这两个边界原样落盘
    temps_row1 = table[1][t_c1:t_c1 + 3]
    t1_vals = [float(x) for x in temps_row1]
    check("A9 温度数值（含负温与 0）",
          t1_vals == [25.5, 26.0, -5.5], f"{temps_row1}")
    # 温度小数位口径（声称"一位小数"，代码是 digits=2 去尾零）
    temps_row2 = table[2][t_c1:t_c1 + 3]
    info("A9' 温度列口径", f"模组2 = {temps_row2}（代码 _num(v,2) 去尾零 → 可能 2 位小数）")

    # F 文件格式
    raw = open(csv_path, "rb").read()
    check("F1 CSV 有 UTF-8 BOM", raw[:3] == b"\xef\xbb\xbf", f"前三字节 {raw[:3]!r}")
    check("F2 CSV 是逗号分隔（无分号/制表）",
          b";" not in raw and b"\t" not in raw and b"," in raw, "含逗号、无分号/制表")
    widths = {len(r) for r in table}
    check("F3 每行列数与表头一致", widths == {len(head)}, f"行长集合 {sorted(widths)} 表头 {len(head)}")

    json_path = os.path.join(OUT, "v25_export_basic.json")
    n = sess.write_json(json_path, meta={"app": "probe"})
    doc = json.load(open(json_path, encoding="utf-8"))
    check("F4 JSON 可 json.load 且轮数一致", n == 1 and len(doc["rounds"]) == 1,
          f"write_json 返回 {n}")
    check("F5 JSON unit 正确", doc["unit"] == {"volt": "mV", "temp": "℃"}, str(doc["unit"]))
    cells0 = doc["rounds"][0]["modules"][0]["cells"]
    from_csv = [int(x) for x in table[1][i_c1:i_c1 + 6]]
    check("A10 JSON cells 是 int 且与 CSV 一致",
          all(isinstance(v, int) for v in cells0) and cells0 == from_csv,
          f"{cells0} vs CSV {from_csv}")
    check("A11 JSON 模组数/温度数",
          [len(m["cells"]) for m in doc["rounds"][0]["modules"]] == [6, 6, 6]
          and [len(m["temps"]) for m in doc["rounds"][0]["modules"]] == [3, 3, 3],
          f"{[len(m['cells']) for m in doc['rounds'][0]['modules']]}")


# ------------------------------------------------------------------ C 分包
def case_split() -> None:
    """一帧电压拆成 3 个 Voltage1: 帧，每帧只带一部分数字。"""
    sess = rx_export.Session()
    sess.configure([4, 4], [2, 2])
    seq = ["3.8712", "3.8713", "3.8714", "3.8715",
           "3.8716", "3.8717", "3.8718", "3.8719"]
    sess.feed("Voltage1:\n" + " ".join(seq[:3]))
    sess.feed("Voltage1:\n" + " ".join(seq[3:5]))
    sess.feed("Voltage1:\n" + " ".join(seq[5:]))
    sess.feed("BMS OK")
    rounds = sess.rounds()
    check("C1 分包帧仍是 1 轮", len(rounds) == 1, f"{len(rounds)} 轮")
    if rounds:
        got = [m for mod in rounds[0]["volt"] for m in mod]
        check("C2 电芯按顺序拼起来（8 个不多不少）",
              got == [float(x) for x in seq], f"{got}")
        check("C3 切成 2 模组 × 4",
              [len(m) for m in rounds[0]["volt"]] == [4, 4],
              f"{[len(m) for m in rounds[0]['volt']]}")


# ------------------------------------------------------------ D 异常输入
def case_messy() -> None:
    sess = rx_export.Session()
    sess.configure([3, 3, 3], [2, 2, 2])
    # 行尾 \r（CRLF）+ 行首缩进 + 标题后带空格 + 数字之间多空格
    frame = ("   Voltage1:   \r\n"
             "   3.8712     3.8001\t3.7999   \r\n"
             " 4.1234      4.0000     0.0000\r\n"
             "3.9000  3.9001  3.9000\r\n"
             "\r\n"
             "  Temperature1:  \r\n"
             "25.5   26.0\r\n"
             "-5.5   0.0\r\n"
             "1.25   2.25\r\n")
    sess.feed(frame)
    # 故意**不发 BMS**，下一轮直接来 Voltage1:（用户点名的"缺 BMS 行"）
    sess.feed("Voltage1:\n 3.8800 3.8801 3.8802\n 3.8803 3.8804 3.8805\n"
              "3.8806 3.8807 3.8808\n")
    sess.feed("BMS OK")
    rounds = sess.rounds()
    check("D1 缺 BMS 也能切出新轮（2 轮，不串行）", len(rounds) == 2, f"{len(rounds)} 轮")
    if rounds:
        r0 = rounds[0]
        flat = [v for m in r0["volt"] for v in m]
        check("D2 \\r/缩进/多空格：第一轮电芯值不串",
              flat == [3.8712, 3.8001, 3.7999, 4.1234, 4.0, 0.0,
                       3.9, 3.9001, 3.9], f"{flat}")
        check("D3 温度段切分（3 模组 × 2）",
              [len(m) for m in r0["temp"]] == [2, 2, 2], f"{[len(m) for m in r0['temp']]}")
        r1 = rounds[1]
        check("D4 第二轮只有电压、没混进上一轮的温度",
              not r1["temp"] and len([v for m in r1["volt"] for v in m]) == 9,
              f"volt={[len(m) for m in r1['volt']]} temp={r1['temp']}")


def case_header_inline() -> None:
    """标题和数据在**同一行**（`Voltage1: 3.8712 3.8001`）—— 设备可能这么发。"""
    sess = rx_export.Session()
    sess.configure([3], [1])
    sess.feed("Voltage1: 3.8712 3.8001 3.7999")
    sess.feed("BMS OK")
    rounds = sess.rounds()
    got = [v for m in (rounds[0]["volt"] if rounds else []) for v in m]
    ok = got == [3.8712, 3.8001, 3.7999]
    check("D5 标题与数据同一行（Voltage1: 1.2 3.4）应能解析", ok, f"实得 {got}")

    # 更糟的一种：先有正常标题进了电压段，后面某一帧又变成「标题+数据」同一行
    # （设备分包/蓝牙广播都会这样，见 selftest 的 b"Voltage1: 01.3700\n"）
    sess2 = rx_export.Session()
    sess2.configure([3], [1])
    sess2.feed("Voltage1:\n3.8712")
    sess2.feed("Voltage1: 3.8001 3.7999")      # 同一行的标题里那个 "1" 会不会被当数
    sess2.feed("BMS OK")
    r2 = sess2.rounds()
    flat2 = [v for m in (r2[0]["volt"] if r2 else []) for v in m]
    check("D6 续帧写成「Voltage1: 值 值」不能把标题里的 1 当数据",
          flat2 == [3.8712, 3.8001, 3.7999], f"实得 {flat2}")


# ---------------------------------------------------------- E 残缺模组
def case_partial() -> None:
    sess = rx_export.Session()
    sess.configure([25, 25, 25], [8, 8, 8])
    sess.feed("Voltage1:\n 3.8712 3.8713 3.8714")
    sess.feed("BMS OK")
    rounds = sess.rounds()
    csv_path = os.path.join(OUT, "v25_export_partial.csv")
    rows = sess.write_csv(csv_path)
    table = read_csv(csv_path)
    check("E1 残缺模组不整轮丢（1 轮 1 行）",
          len(rounds) == 1 and rows == 1, f"{len(rounds)} 轮 / {rows} 行")
    if table[1:]:
        head, row = table[0], table[1]
        i_c1 = head.index("电芯1(mV)")
        cells = [x for x in row[i_c1:i_c1 + 25] if x != ""]
        check("E2 保留 3 个值且落在电芯1..3",
              cells == ["3871", "3871", "3871"], f"{cells[:5]}…（共 {len(cells)}）")
        n_cell_cols = len([h for h in head if h.startswith("电芯")])
        check("E3 列宽按实际数据补（配置 25 只来 3 → 3 列，不报错）",
              n_cell_cols == 3 and len(row) == len(head),
              f"电芯列 {n_cell_cols} 个，行长 {len(row)} / 表头 {len(head)}")


# ---------------------------------------------------------- G 500 轮上限
def case_cap() -> None:
    sess = rx_export.Session()
    sess.configure([2], [1])
    for i in range(520):
        sess.feed(f"Voltage1:\n {3.8000 + i / 10000.0:.4f} 3.8000")
        sess.feed("Temperature1:\n 20.0")
        sess.feed("BMS OK")
    rounds = sess.rounds()
    ok = len(rounds) == 500
    first = rounds[0]["volt"][0][0] if rounds else None
    check("G1 只留最近 500 轮", ok, f"{len(rounds)} 轮")
    check("G2 留下的是**最近**的（第 21 轮起）",
          abs((first or 0) - (3.8 + 20 / 10000.0)) < 1e-9,
          f"首轮首值 {first} 期望 {3.8 + 20 / 10000.0:.4f}")


# ------------------------------------------- H 真机格式（序号.mV）
def case_real_log() -> None:
    """用**用户真机**的长日志（5673 行 / 1194 轮）整段喂进去，数一数能认出几轮。

    真机实际发三种方言（统计自 接收历史-E74-2026.log）：
      A  `VoltageN:` 独占行 + `01.3851 02.3852 …`（序号.mV，914 轮）
      B  `Voltage1: 3300 3301 …`（标题与数据**同一行**、纯 mV 整数，280 轮）
      C  `Temperature1: 24 25 26 …`（温度也是同行、纯 ℃，280 轮）
    显示层认 `Voltage\\d*`（ui_highlight.VOLT_HEADER_RE），导出层只认 `Voltage1`。
    """
    import re
    path = os.path.expanduser("~/.serialassistant/logs/接收历史-E74-2026.log")
    if not os.path.isfile(path):
        info("R0 真机长日志不存在，跳过", path)
        return
    text = open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    truth = len([ln for ln in text.split("\n")
                 if re.match(r"\s*Voltage\d*\s*:", ln) and ln.strip()])
    sess = rx_export.Session()
    sess.configure([25] * 6, [4] * 6)
    sess.feed(text)
    sess.feed("BMS OK")
    rounds = sess.rounds()
    cells = [v for r in rounds for m in r["volt"] for v in m]
    info("R0 真机日志", f"{len(text.splitlines())} 行 / 设备轮数 {truth} / BMS "
                         f"{text.upper().count('BMS')} 次")
    check("R1 真机日志能被切成接近设备轮数（≥90%）",
          len(rounds) >= truth * 0.9, f"导出 {len(rounds)} 轮 / 应为 {truth} 轮")
    bad = [v for v in cells if not (1.0 <= v <= 5.0) and not (1000 <= v <= 5000)]
    check("R2 电芯值都在 1000~5000 mV 的合理区间",
          cells and not bad, f"{len(cells)} 个值，越界 {len(bad)} 个，"
                             f"min/max = {(min(cells), max(cells)) if cells else None}")


def case_device_format() -> None:
    """真机一帧（取自 logs/接收历史.log 原文）：`01.3851 02.3852 …`。

    项目自己的口径（ui_highlight.value_spans）：'1.3820' 的 **3820** 是 mV，
    小数点前是**序号**。导出必须与显示层同口径，否则 CSV 里的 mV 全是错的。
    """
    import ui_highlight
    probe = ui_highlight.value_spans("01.3851 02.3852", 1000, 9000)
    info("H0 显示层口径（value_spans 命中的区间数）", f"{len(probe)} 条（=2 说明 3851/3852 被判成 mV 采样点）")

    sess = rx_export.Session()
    sess.configure([7, 7, 7], [1, 1, 1])
    real = ("Voltage1:\n"
            "01.3851   02.3852   03.3853   04.3854   05.3855   06.3856   07.3857\n"
            "08.3858   09.3859   10.3850   11.3851   12.3852   13.3853   14.3854\n"
            "15.3855   16.3856   17.3857   18.3858   19.3859   20.3850   21.3851\n"
            "Temperature1:\n1.25    2.25\n3.25    4.25\n5.25    6.25\n"
            "BMS OK\n")
    sess.feed(real)
    rounds = sess.rounds()
    csv_path = os.path.join(OUT, "v25_export_device.csv")
    sess.write_csv(csv_path)
    table = read_csv(csv_path) if rounds else []
    got = table[1][3:10] if len(table) > 1 else []
    exp = ["3851", "3852", "3853", "3854", "3855", "3856", "3857"]
    check("H1 真机格式 `01.3851` 导出的 mV = 3851（不是 1385）",
          got == exp, f"实得 {got} 期望 {exp}")
    temps = table[1][table[0].index("温度1(℃)")] if len(table) > 1 else None
    check("H2 真机温度 `1.25`（=第1点 25℃）导出 25", temps in ("25", "25.0"),
          f"实得 {temps!r}")


def main() -> int:
    _LINES.append("=" * 92)
    _LINES.append("v2.5 验收 A：结构化导出（CSV/JSON）")
    _LINES.append("=" * 92)
    case_basic()
    case_split()
    case_messy()
    case_header_inline()
    case_partial()
    case_cap()
    case_device_format()
    case_real_log()

    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    tail = f"\n合计 {passed}/{total} PASS"
    print(tail)
    _LINES.append(tail)
    with open(os.path.join(OUT, "verify25_export.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(_LINES) + "\n")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
