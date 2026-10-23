"""断线阈值（相邻电芯压差达阈值 → 在异常电芯那一侧插 X）的排版与着色验证。

用户 2026-10-24 定案（原话："如果电压过低，X就只出现在该低压数据的右边，如果
电压过高，X就只出现在该高压数据的左边，一号电芯就用该模组正常电芯电压的平均值
来减去它的值来计算"）：
- 判据：某电芯比**上一个电芯**低/高出一个阈值（一号电芯没有上一个，用**本模组
  正常电芯的平均值**去减它）；
- 位置：**偏低 → X 只在该低压值的右边；偏高 → X 只在该高压值的左边**；
- 不改变格式：X 占用原来的 3 空格列间隔，行宽一格不变。

用法：QT_QPA_PLATFORM=windows python devtools/_probe_wire_break.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QTextEdit  # noqa: E402

from ui_highlight import (  # noqa: E402
    WIRE_BREAK, RxHighlighter, align_indexes, wire_break_spans,
)
from ui_theme import qcolor  # noqa: E402

THR = 60


def row(values: list[int], thr: int = THR) -> str:
    line = " ".join(f"{i}.{v:04d}" for i, v in enumerate(values, 1))
    return align_indexes("Voltage1:\n" + line, wire_mv=thr).split("\n")[1]


def main() -> int:
    app = QApplication(sys.argv)          # noqa: F841

    low = [3800, 3800, 2900, 3800, 3800]
    high = [3800, 3800, 4600, 3800, 3800]
    print("低压:     ", repr(row(low)))
    print("高压:     ", repr(row(high)))
    print("一号偏低: ", repr(row([2900, 3800, 3800, 3800])))
    print("一号偏高: ", repr(row([4600, 3800, 3800, 3800])))
    print("正常:     ", repr(row([3800, 3800, 3800, 3800])))

    # ① 低压：X 只在那个低压值**右边**，且只有一个
    r = row(low)
    assert r.count(WIRE_BREAK) == 1, f"低压只该有一个 X：{r!r}"
    x = r.index(WIRE_BREAK)
    assert r[:x].rstrip().endswith("03.2900"), f"X 没在那个低压值的右边：{r!r}"
    assert len(r) == len(row([3800] * 5)), f"X 改了行宽：{len(r)}"

    # ② 高压：X 只在那个高压值**左边**，且只有一个
    r = row(high)
    assert r.count(WIRE_BREAK) == 1, f"高压只该有一个 X：{r!r}"
    x = r.index(WIRE_BREAK)
    assert r[x + 1:].lstrip().startswith("03.4600"), f"X 没在那个高压值的左边：{r!r}"
    assert len(r) == len(row([3800] * 5)), f"X 改了行宽：{len(r)}"

    # ③ 一号电芯：用模组平均值当参照（它没有"上一个电芯"）
    r = row([2900, 3800, 3800, 3800])
    assert r.count(WIRE_BREAK) == 1 and \
        r[:r.index(WIRE_BREAK)].rstrip().endswith("01.2900"), \
        f"一号偏低：X 该在它右边：{r!r}"
    r = row([4600, 3800, 3800, 3800])
    assert r.startswith(WIRE_BREAK + " "), f"一号偏高：X 该在行首（它的左边）：{r!r}"

    # ③b 真机数据（用户 2026-10-24 截图里的 Voltage2，阈值 300）：
    #    04.4081 → 05.4520 相差 439，但两者离模组平均（≈4313）都只差 200 出头。
    #    ⛔ 之前那道"必须同时偏离平均值 ≥ 阈值"的闸会把这条**真实断线**吞掉。
    real = [4311, 4307, 4325, 4081, 4520, 4308, 4310, 4311, 4314, 4310, 4311, 4314]
    r = row(real, thr=300)
    print("真机(300):", repr(r))
    assert r.count(WIRE_BREAK) == 1, f"真实断线没被标出来：{r!r}"
    x = r.index(WIRE_BREAK)
    assert r[:x].rstrip().endswith("04.4081"), f"X 该在低值 04.4081 右边：{r!r}"
    assert r[x + 1:].lstrip().startswith("05.4520"), f"X 后面该是 05.4520：{r!r}"

    # ④ 正常数据一个都不插；阈值 0 = 关闭
    assert WIRE_BREAK not in row([3800, 3801, 3799, 3800])
    assert WIRE_BREAK not in row(low, thr=0)

    # ⑤ 断一次只出一个 X（不在两侧各来一个）
    for vals in ([3800, 2900, 3800], [3800, 4600, 3800], [2900, 3800], [4600, 3800]):
        got = row(vals)
        assert got.count(WIRE_BREAK) == 1, f"{vals} 出了多个 X：{got!r}"

    # ⑥ 温度段不插（温度没有电芯/采样线概念）
    assert WIRE_BREAK not in align_indexes("Temperature1:\n1.22 2.22 3.22", wire_mv=1)

    # ⑦ 跨行：模组 2 行、**第 2 行首个电芯**偏高 → 它的左侧就是换行处，
    #    X 落在第 1 行末尾（这样既不破坏列对齐，又紧挨着那个高压值）
    two = align_indexes("Voltage1:\n"
                        + " ".join(f"{i}.3800" for i in range(1, 5)) + "\n"
                        + " ".join(["5.4600", "6.3800"]), wire_mv=THR).split("\n")[1:]
    for line in two:
        print("两行模组:", repr(line))
    assert two[0].rstrip().endswith(WIRE_BREAK), f"跨行边界没落在上一行末尾：{two}"

    # ⑧ 着色
    box = QTextEdit()
    hl = RxHighlighter(box.document())
    box.setPlainText(align_indexes(
        "Voltage1:\n" + " ".join(f"{i}.{v:04d}" for i, v in enumerate(low, 1)),
        wire_mv=THR))
    hl.rehighlight()
    blk = box.document().findBlockByNumber(1)
    want = qcolor("wire_break").name()
    spans = [(r.start, r.format.foreground().color().name(), r.format.fontWeight())
             for r in (blk.layout().formats() if blk.layout() else [])]
    xs = [s for s in spans if s[1] == want]
    print("X span:", xs)
    assert len(xs) == 1 and xs[0][2] > 400, f"X 没上色/没加粗：{spans}"
    assert len(wire_break_spans(blk.text())) == 1
    box.deleteLater()
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
