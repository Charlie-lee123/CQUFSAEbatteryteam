"""验证三件事（用真实历史数据当素材）：

A. 历史整篇排版 == 实时逐帧排版的观感（拿同一轮分别按两种方式排版后逐字比）。
B. 设备漏发 "Voltage1:" 时，接在 BMS 错误块后面的电压行**不再整行铺黄**，
   电压值仍按 mV 阈值判红/绿。
C. section_of 的自愈不会把温度行误判成电压行。

用法：python devtools/_probe_history_format.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QTextEdit  # noqa: E402

from ui_highlight import (  # noqa: E402
    SECTION_OTHER, SECTION_TEMP, SECTION_VOLT, RxHighlighter, align_indexes,
    section_of,
)

LOG = ROOT / "发布/logs/接收历史.log"


def _rounds(lines: list[str]) -> list[list[str]]:
    """按时间戳切轮次。"""
    out: list[list[str]] = []
    cur: list[str] = []
    for line in lines:
        if line.startswith("[") and cur:
            out.append(cur)
            cur = [line]
        else:
            cur.append(line)
    if cur:
        out.append(cur)
    return out


def main() -> int:
    app = QApplication(sys.argv)          # noqa: F841 - 高亮器要 QApplication
    raw = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    rounds = [r for r in _rounds(raw)
              if any(ln.startswith("Voltage1:") for ln in r)]
    print(f"共 {len(raw)} 行 / {len(rounds)} 轮（含 Voltage1 的）")

    # ---- A. 整篇排版 vs 单轮排版 ----
    t0 = time.perf_counter()
    whole = align_indexes("\n".join(raw), section=SECTION_OTHER)
    cost = (time.perf_counter() - t0) * 1000
    whole_lines = whole.split("\n")
    print(f"A. 整篇排版耗时 {cost:.1f}ms（{len(raw)} 行）")

    sample = rounds[-1] if rounds else []
    if sample:
        # 实时路径：一块 = 一轮（同一帧）→ 一次 align_indexes
        live = align_indexes("\n".join(sample), section=SECTION_OTHER).split("\n")
        # 整篇里按**首行（时间戳）文本**定位同一轮，从那儿起取同样行数
        anchor = live[0] if live else ""
        tail_start = None
        for i in range(len(whole_lines) - 1, -1, -1):
            if whole_lines[i] == anchor:
                tail_start = i
                break
        got = whole_lines[tail_start: tail_start + len(live)] \
            if tail_start is not None else []
        print(f"   最后一轮（锚点 {anchor}）：实时排版 {len(live)} 行 / "
              f"整篇里取到 {len(got)} 行")
        same = got == live
        print(f"   => {'✓ 完全一致' if same else '✗ 不一致'}")
        if not same:
            for n, (a, b) in enumerate(zip(live, got)):
                if a != b:
                    print(f"      第{n}行 实时 {a!r}")
                    print(f"      第{n}行 整篇 {b!r}")
                    break
        # 观感抽查：电压行序号补零 + 列对齐；温度行序号不补零
        for line in live:
            if line.startswith(("0", "1", "2")) and line.count(".") >= 4:
                print(f"   电压行样例：{line[:88]!r}")
                break
        for line in live:
            if line.startswith("1.2") and line.count(".") >= 3:
                print(f"   温度行样例：{line[:88]!r}")
                break

    # ---- B. 漏发 Voltage1 的电压行不能被 BMS 块铺黄 ----
    leak = [
        "Temperature1:",
        "1.25   2.25   3.25",
        "BMS Error",
        "errorTime=0",
        "5.8 Over Temp",
        "CVMax=3866, Min=0",
        # ⛔ 这里故意**不发** "Voltage1:"：设备真实存在的轮次形态
        "01.3858   02.3859   03.3860   04.3861",
        "05.3862   06.3863   07.3864   08.3865",
    ]
    doc = QTextEdit()
    hl = RxHighlighter(doc.document())
    doc.setPlainText("\n".join(leak))
    hl.rehighlight()
    # 逐块看 block layout 里的 format 区间：某一段覆盖了整行且带背景色 = 整行铺色
    rows = []
    block = doc.document().begin()
    while block.isValid():
        text = block.text()
        spans = block.layout().formats() if block.layout() else []
        bms_painted = False
        for rng in spans:
            if rng.length >= max(1, len(text) - 1) \
                    and rng.format.background().color().alpha() > 0:
                bms_painted = True
        rows.append((text, bms_painted, len(spans)))
        block = block.next()
    print("B. 每个块的铺色判定：")
    bad = []
    for text, painted, n in rows:
        flag = "整行铺色" if painted else "按条着色"
        print(f"   [{flag}] {text[:56]!r}  ({n} 段)")
        if text.startswith(("01.3858", "05.3862")) and painted:
            bad.append(text)
    print("   => " + ("✗ 电压行仍被整行铺黄" if bad else "✓ 电压行不再整行铺黄"))

    # ---- C. 段状态自愈的方向 ----
    s = SECTION_OTHER
    for line in ("Temperature1:", "1.25 2.25"):
        s = section_of(line, s)
    t_state = s
    s = section_of("01.3858 02.3859", s)
    print(f"C. 温度段({t_state}) → 见到 4 位小数数据行后 = {s}（应为 {SECTION_VOLT}）")
    s2 = section_of("1.25 2.25 3.25", SECTION_VOLT)
    print(f"   电压段里见到 2 位小数行 = {s2}（应保持 {SECTION_VOLT}，温度行不抢段）")
    s3 = section_of("Temperature2:", SECTION_OTHER)
    print(f"   Temperature2: → {s3}（应为 {SECTION_TEMP}）")
    doc.deleteLater()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
