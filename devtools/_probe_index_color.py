"""最小复现：接收区里"Voltage1: + 一行电压数据"的着色，与阈值/段状态一起打印。

用法：QT_QPA_PLATFORM=windows python devtools/_probe_index_color.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QSettings  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main_window as mw  # noqa: E402
from ui_highlight import index_spans  # noqa: E402
from ui_theme import rx_accent, ui_font  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    try:
        w = mw.MainWindow()
        w.show()
        QTest.qWait(400)
        print("volt_limits =", w._rx_highlighter.volt_limits())
        print("temp_limits =", w._rx_highlighter.temp_limits())
        w._clear_receive()
        w._on_received("Voltage1:\n 1.3820   2.3824   3.3827\n".encode("utf-8"))
        w._flush_rx()
        app.processEvents()
        doc = w.txt_rx.document()
        for n in range(doc.blockCount()):
            blk = doc.findBlockByNumber(n)
            spans = [(r.start, r.length, r.format.foreground().color().name())
                     for r in (blk.layout().formats() if blk.layout() else [])]
            print(f"块{n} {blk.text()!r}")
            print(f"     spans={spans}")
            if n == 2:
                offs = index_spans(blk.text())
                print(f"     序号位置={sorted(offs)}  主题蓝={rx_accent().name()}")
                picked = [(ln, c) for s, ln, c in spans if s in offs]
                print(f"     落在序号上的 span={picked}")
        print("_align_section =", mw.SECTION_OTHER, "->", w._align_section)
        w.close()
        w.deleteLater()
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    finally:
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
