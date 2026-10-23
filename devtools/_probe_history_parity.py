"""对照实验：实时显示的文本 vs 回放出来的文本，必须**逐行一模一样**。

2026-10-24 用户点名："怎么出现了该空行的地方没有空行，不应该空行的地方空行了…
历史数据格式得和接收时一模一样"。
这里直接拿同一批帧跑两遍（实时 / 历史回放），逐行 diff，把"空行多一个/少一个"
钉死在数字上。

用法：QT_QPA_PLATFORM=windows python devtools/_probe_history_parity.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "_check"

from PySide6.QtCore import QEvent, QSettings  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import rx_history  # noqa: E402
from main_window import MainWindow  # noqa: E402
from ui_theme import ui_font  # noqa: E402

CELLS = 25
TEMPS = 8


def frames() -> list[str]:
    """两轮真机形态的数据：每轮 电压(5 模组) → 温度(5 模组) → BMS 明细。"""
    out = []
    for r in range(2):
        lines = ["Voltage1:"]
        for m in range(5):
            if m:
                lines.append(f"Voltage{m + 1}:")
            vals = [f"{i + 1}.{3800 + (r + i + m) % 90:04d}" for i in range(CELLS)]
            lines.append(" ".join(vals[:12]))
            lines.append(" ".join(vals[12:]))
        for m in range(5):
            lines.append(f"Temperature{m + 1}:")
            temps = [f"{i + 1}.{20 + (r + i + m) % 9:02d}" for i in range(TEMPS)]
            lines.append(" ".join(temps[:4]))
            lines.append(" ".join(temps[4:]))
        if r == 1:
            # ⚠️ 故意在 BMS 前面**多塞一个空行**（设备分包时有这种毛刺）：
            #    实时不该显示它（空行只允许出现在轮间），回放更不该多出来。
            lines.append("")
            lines.append("BMS Error")          # 混大小写：真机就是这么发的
            lines.append("errorTime=0")
            lines.append("5.8 Over Temp")
            lines.append("COM0=1")
        else:
            lines.append("BMS OK")
        out.append("\n".join(lines))
    return out


def dump(text: str) -> list[str]:
    return text.split("\n")


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    saved = (rx_history._path, rx_history._file_lines, tuple(rx_history._lines))
    window = None
    try:
        window = MainWindow()
        window.resize(1180, 860)
        window.show()
        QTest.qWait(600)
        window._clear_receive()
        # ⛔ 建窗时 rx_history.load() 已经把**真实历史**读进内存了（几千行）：
        #    不清掉的话下面 A/B 两段会被旧数据污染，diff 全是噪声。
        rx_history._lines.clear()
        rx_history._pending.clear()
        rx_history._file_lines = 0

        # ---------- A. 实时 ----------
        sent = frames()
        for chunk in sent:
            window._on_received(chunk.encode("utf-8"))
            window._flush_rx(force=True)
            app.processEvents()
        live = dump(window.txt_rx.toPlainText())

        # 历史里记下来的（= feed 收到的行）
        stored = list(rx_history._lines)
        print(f"A 实时：{len(live)} 行；历史：{len(stored)} 行")

        # ---------- B. 回放 ----------
        tmpdir = Path(tempfile.mkdtemp(prefix="parity_", dir=str(OUT)))
        tmp = tmpdir / rx_history.FILE_NAME
        tmp.write_text("\n".join(stored) + "\n", encoding="utf-8")
        rx_history._path = tmp
        rx_history._lines.clear()
        rx_history._lines.extend(stored)
        rx_history._file_lines = len(stored)
        window._open_history()
        QTest.qWait(500)
        app.processEvents()
        replay = dump(window.txt_rx.toPlainText())
        print(f"B 回放：{len(replay)} 行")

        # ---------- 逐行 diff ----------
        diff = []
        for i in range(max(len(live), len(replay))):
            a = live[i] if i < len(live) else "<缺失>"
            b = replay[i] if i < len(replay) else "<缺失>"
            if a != b:
                diff.append((i, a, b))
        print(f"差异行：{len(diff)}")
        for i, a, b in diff[:12]:
            print(f"  L{i}: 实时 {a!r}")
            print(f"       回放 {b!r}")

        # 空行统计（这是用户这次最在意的）
        def blank_idx(rows):
            return [i for i, t in enumerate(rows) if not t.strip()]
        bl_live, bl_replay = blank_idx(live), blank_idx(replay)
        print(f"空行位置：实时 {bl_live}")
        print(f"          回放 {bl_replay}")

        OUT.mkdir(exist_ok=True)
        # ---- 空行规则 & BMS 着色 ----
        # 规则（2026-10-24 用户点名）：每个 VoltageN: / TemperatureN: / BMS /
        # 时间戳**之上**各空一行，其它位置一个空行都没有。
        def _heads(line: str) -> bool:
            return line.startswith(("[", "Voltage", "Temperature", "BMS"))

        bad = []
        for i, t in enumerate(live):
            if not t.strip():
                if not (i + 1 < len(live) and _heads(live[i + 1])):
                    bad.append(("多余空行", i, live[max(0, i - 1):i + 2]))
            elif i > 0 and _heads(t) and live[i - 1] != "":
                bad.append(("缺空行", i, live[max(0, i - 2):i + 1]))
        blanks = blank_idx(live)
        print(f"时间戳 {len([t for t in live if t.startswith('[')])} 个 / 模组标题 "
              f"{len([t for t in live if t.startswith(('Voltage', 'Temperature'))])} 个 / "
              f"空行 {len(blanks)} 个")
        assert not bad, f"空行位置不合规则：{bad[:3]}"
        assert blank_idx(replay) == blanks, \
            f"回放空行与实时不一致：{blank_idx(replay)} vs {blanks}"
        # BMS Error 必须着色（大小写不敏感），且只能标那几个字
        doc = window.txt_rx.document()
        bms_row = next((i for i in range(doc.blockCount())
                        if "BMS Error" in doc.findBlockByNumber(i).text()), None)
        if bms_row is None:
            print("BMS Error 行：不在画面里")
        else:
            blk = doc.findBlockByNumber(bms_row)
            spans = blk.layout().formats() if blk.layout() else []
            print(f"BMS Error 行着色：{[r.format.foreground().color().name() for r in spans]}")
            assert spans, "BMS Error 没有被着色（大小写不敏感没生效？）"
        (OUT / "history_parity.json").write_text(json.dumps({
            "live": live, "replay": replay, "stored": stored,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        # 留一张实拍给用户看：轮间空行 + BMS Error 标色 + 无多余空行
        shot = window._rx_card.grab()
        shot.save(str(OUT / "parity_visual.png"))
        print("截图:", OUT / "parity_visual.png")
        (OUT / "history_parity.txt").write_text(
            "\n".join(f"{i:4d} LIVE |{a}|\n     RPLY |{b}|"
                      for i, (a, b) in enumerate(zip(live + [""] * 9,
                                                     replay + [""] * 9))
                      if a or b), encoding="utf-8")
        print("明细:", OUT / "history_parity.txt")
    finally:
        if window is not None:
            window.close()
            window.deleteLater()
            app.processEvents()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
        rx_history._path, rx_history._file_lines, lines = saved
        rx_history._lines.clear()
        rx_history._lines.extend(lines)
        rx_history._pending.clear()
        settings = QSettings("MimoWorks", "SerialAssistant")
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
