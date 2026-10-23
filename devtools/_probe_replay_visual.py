"""真窗口验证历史回放：排版是否与实时一致、打开后是否停在最新一轮。

用法：QT_QPA_PLATFORM=windows python devtools/_probe_replay_visual.py
输出：_check/replay_visual.png（接收区截图）+ 控制台数值
"""
from __future__ import annotations

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
import ui_settings  # noqa: E402,F401 - 预热，避免首帧卡在导入
from main_window import MainWindow  # noqa: E402
from ui_theme import ui_font  # noqa: E402

ROUNDS = 26
CELLS = 25
TEMPS = 20


def make_history() -> list[str]:
    """按真机格式造 N 轮：Voltage1..3 / Temperature1..3 / BMS 明细。"""
    out: list[str] = []
    for r in range(ROUNDS):
        out.append(f"[2026-10-23 2{r % 24}:{(r * 7) % 60:02d}:00]")
        for m in range(3):
            out.append(f"Voltage{m + 1}:")
            cells = [f"{i + 1}.{3800 + (r + i + m) % 90:04d}" for i in range(CELLS)]
            out.append(" ".join(cells[:12]))
            out.append(" ".join(cells[12:]))
        for m in range(3):
            out.append(f"Temperature{m + 1}:")
            temps = [f"{i + 1}.{24 + (r + i + m) % 6:02d}" for i in range(TEMPS)]
            out.append(" ".join(temps[:4]))
            out.append(" ".join(temps[4:]))
        out.append("BMS ERROR" if r % 5 == 4 else "BMS OK")
        if r % 5 == 4:
            out.append("errorTime=0")
            out.append("5.8 Over Temp")
            out.append("COM0=1")
    return out


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
        QTest.qWait(700)
        app.processEvents()
        window._clear_receive()

        # 建窗之后再种历史（与自测同一套路：load() 已跑完，不会被真实文件覆盖）
        rows = make_history()
        tmpdir = Path(tempfile.mkdtemp(prefix="replaycheck_", dir=str(ROOT / "_check")))
        tmp = tmpdir / rx_history.FILE_NAME
        tmp.write_text("\n".join(rows), encoding="utf-8")
        rx_history._path = tmp
        rx_history._lines.clear()
        rx_history._lines.extend(rows)
        rx_history._file_lines = len(rows)

        window._open_history()
        QTest.qWait(700)
        app.processEvents()

        bar = window.txt_rx.verticalScrollBar()
        doc = window.txt_rx.document()
        last = doc.findBlockByNumber(doc.blockCount() - 1)
        geom = window.txt_rx.blockBoundingGeometry(last).translated(
            window.txt_rx.contentOffset())
        view_h = window.txt_rx.viewport().height()
        shown = window.txt_rx.toPlainText().split("\n")
        stamps = [t for t in shown if t.startswith("[")]
        print(f"回放态={window._replay_active}  行数={len(shown)}  轮数={len(stamps)}")
        print(f"滚动条 {bar.value()} / {bar.maximum()}（贴底 = 值等于最大）")
        print(f"最后一块 y={geom.y():.0f}, 视口高={view_h} -> "
              f"{'在视口内' if 0 <= geom.y() < view_h else '不在视口内'}")
        print(f"最后一轮时间戳={stamps[-1] if stamps else '—'}")
        volt = [t for t in shown if t.startswith("01.")]
        temp = [t for t in shown if t.startswith("1.2")]
        print(f"电压行样例（补零+等宽）: {volt[-1][:92] if volt else '—'!r}")
        print(f"温度行样例（不补零）    : {temp[-1][:92] if temp else '—'!r}")
        assert bar.value() >= bar.maximum() - 1, "打开历史后没有停在最新（没贴底）"
        assert 0 <= geom.y() < view_h, "最新一块不在视口里"
        assert volt and volt[-1].startswith("01."), "历史电压行没有补零（没走排版）"
        assert temp and temp[-1].startswith("1."), "历史温度行序号被补零了"

        OUT.mkdir(exist_ok=True)
        shot = window._rx_card.grab()
        shot.save(str(OUT / "replay_visual.png"))
        print("截图:", OUT / "replay_visual.png")
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
