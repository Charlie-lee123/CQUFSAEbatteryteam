# -*- coding: utf-8 -*-
r"""底栏 SOC 四枚标签的视觉取样（不改用户配置）。

构造真实 MainWindow → 注入 E74（只打补丁 current_vehicle，不调 select_vehicle）
→ 喂固定合成报文 → 抓底栏 QWidget 截图，供人眼确认排版。

⛔ QSettings 前后快照比对，finally 还原；不写 geometry、不改主题。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "_check"
OUT.mkdir(exist_ok=True)

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import customization  # noqa: E402
import rx_history  # noqa: E402


def block(i_meas: float, closed: bool) -> str:
    cells = [3200] * 125
    cells[0] = 3190
    cells[124] = 3210
    cells[11] = 0
    cells[22] = 5000
    out = []
    for m in range(5):
        out.append(f"Voltage{m + 1}:")
        out.append("   ".join(
            f"{s + 1:02d}.{cells[m * 25 + s]}" for s in range(25)))
    for m in range(5):
        out.append(f"Temperature{m + 1}:")
        out.append("   ".join(f"{s + 1}.40" for s in range(8)))
    air = "AIR+=1,AIR-=1,PRE=1" if closed else "AIR+=0,AIR-=0,PRE=0"
    out += ["BMS OK", "COM0=0", air,
            f"V=0.00,I={i_meas:.2f}", "CVMax=3210, Min=3190"]
    return "\n".join(out)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    qs = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: qs.value(k) for k in qs.allKeys()}

    tmp = OUT / "_bar_tmp"
    tmp.mkdir(exist_ok=True)
    rx_history._resolve_dir = lambda: tmp
    customization.current_vehicle = lambda: "E74-2026"
    try:
        import main_window
        win = main_window.MainWindow()
        win.show()
        app.processEvents()

        cases = [
            ("1100_放电", 1100, 720, 12.0, True),
            ("820_母线断开", 820, 700, 1.89, False),
            ("820_充电", 820, 700, -12.0, True),
        ]
        for tag, w, h, cur, closed in cases:
            win.resize(w, h)
            app.processEvents()
            win._feed_soc(block(cur, closed))
            app.processEvents()
            bar = win._bottom_bar
            # ⛔ 不能 bar.grab()：QWidget 没设 WA_StyledBackground 时 QSS 的
            #    background 不参与绘制，抓到的是白底 —— 深色主题的近白文字
            #    就会「看不见」，看起来像 bug，其实是取样伪影。抓整窗再裁。
            from PySide6.QtCore import QPoint as _QP
            full = win.grab()
            off = bar.mapTo(win, _QP(0, 0))
            # ⛔ 高 DPI 下 grab() 给的是 device pixels（dpr=2 时 2 倍），
            #    而 bar.height() 是逻辑像素 —— 不换算会裁到别处去。
            dpr = full.devicePixelRatio() or 1.0
            top = int(round(off.y() * dpr))
            bh = int(round(bar.height() * dpr))
            top = max(0, min(top, full.height() - bh))
            pix = full.copy(0, top, full.width(), min(bh, full.height() - top))
            path = OUT / f"socbar_{tag}.png"
            pix.save(str(path))
            from PySide6.QtGui import QPalette
            diag = []
            for lbl in win._soc_labels:
                c = lbl.palette().color(QPalette.WindowText)
                diag.append(
                    f"{lbl.objectName() or '?'}({lbl.text()!r} "
                    f"em={lbl.property('emphasis')} st={lbl.property('state')} "
                    f"rgb={c.red()},{c.green()},{c.blue()} "
                    f"bold={lbl.font().bold()} wt={lbl.font().weight()} "
                    f"geo={lbl.x()}+{lbl.width()})")
            bgbg = bar.palette().color(QPalette.Window)
            print(f"\n[{tag}] {pix.width()}x{pix.height()} "
                  f"barbg={bgbg.red()},{bgbg.green()},{bgbg.blue()}")
            for d in diag:
                print("   " + d)
        return 0
    finally:
        for k in qs.allKeys():
            if k not in snap:
                qs.remove(k)
        for k, v in snap.items():
            qs.setValue(k, v)
        qs.sync()


if __name__ == "__main__":
    sys.exit(main())
