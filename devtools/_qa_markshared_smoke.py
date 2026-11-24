# -*- coding: utf-8 -*-
"""QA 冒烟：只验证探针脚手架（隔离目录 / 真窗口 / 灌数 / 打标 / 几何读数）。

不判定需求，只为正式探针 `_qa_markshared_flow.py` 摸清读数方式。
运行：QT_QPA_PLATFORM=windows .venv\\Scripts\\python.exe devtools\\_qa_markshared_smoke.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import rx_history
import rx_store
from ui_highlight import stamp_span


def feed_round(win, stamp: str, base: int) -> None:
    win._emit_block(
        f"[{stamp}]\nVoltage1:\n"
        f"{base:02d}.3800 {base + 1:02d}.3820 {base + 2:02d}.3900\n"
        f"Temperature1:\n1.25 2.25 3.25\nBMS OK")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from ui_theme import ui_font
    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))

    qs = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: qs.value(k) for k in qs.allKeys()}
    check_dir = Path(_ROOT) / "_check"
    hist_dir = Path(tempfile.mkdtemp(prefix="qa_ms_hist_", dir=check_dir))
    db_dir = Path(tempfile.mkdtemp(prefix="qa_ms_db_", dir=check_dir))
    real_resolver = rx_history._resolve_dir
    real_db = rx_store.db_path()
    win = None
    try:
        rx_history._resolve_dir = lambda: hist_dir
        rx_store.set_db_path(str(db_dir / "sessions.db"))
        import main_window
        win = main_window.MainWindow()
        win.resize(1200, 800)
        win.show()
        app.processEvents()
        win.chk_timestamp.setChecked(True)
        win._marks.clear()
        win._refresh_mark_list()

        for i, stamp in enumerate(("[2026-11-24 10:00:0%d]" % i for i in range(4))):
            feed_round(win, stamp, 1 + i * 3)
        app.processEvents()

        doc = win.txt_rx.document()
        print("blockCount:", doc.blockCount())
        stamps = [(b, doc.findBlockByNumber(b).text()) for b in range(doc.blockCount())
                  if stamp_span(doc.findBlockByNumber(b).text())]
        print("stamp blocks:", stamps)
        print("blocks:")
        for b in range(doc.blockCount()):
            print("  ", b, repr(doc.findBlockByNumber(b).text()))
        print("viewport size:", win.txt_rx.viewport().size())
        print("bar:", win.txt_rx.verticalScrollBar().minimum(),
              win.txt_rx.verticalScrollBar().maximum(),
              win.txt_rx.verticalScrollBar().value())
        print("firstVisible:", win.txt_rx.firstVisibleBlock().blockNumber())
        print("viewed:", win._viewed_block_number())
        print("single_line_height:", win._single_line_height())
        print("span(viewed):", win._round_span(win._viewed_block_number()))
        win._mark_current()
        app.processEvents()
        print("marks:", [(m.key, m.start_block, m.stamp) for m in win._marks.marks])
        print("btn_mark en:", win.btn_mark.isEnabled(),
              "list en:", win._mark_list.isEnabled(),
              "list count:", win._mark_list.count())
        # 回放
        win._open_history()
        app.processEvents()
        print("replay:", win._replay_active, "kind:", win._replay_kind,
              "blocks:", win.txt_rx.document().blockCount(),
              "marks:", len(win._marks.marks), "list:", win._mark_list.count())
        print("btn_mark en(replay):", win.btn_mark.isEnabled(),
              "list en(replay):", win._mark_list.isEnabled())
    finally:
        if win is not None:
            win.close()
            win.deleteLater()
        app.processEvents()
        rx_history._resolve_dir = real_resolver
        rx_store.set_db_path(real_db)
        for k in list(qs.allKeys()):
            if k not in snap:
                qs.remove(k)
        for k, v in snap.items():
            qs.setValue(k, v)
        qs.sync()
        shutil.rmtree(hist_dir, ignore_errors=True)
        shutil.rmtree(db_dir, ignore_errors=True)
    now = {k: qs.value(k) for k in qs.allKeys()}
    print("QSettings same:", now == snap, len(snap), "->", len(now))
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
