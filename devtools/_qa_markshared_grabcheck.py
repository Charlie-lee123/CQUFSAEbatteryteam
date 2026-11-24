# -*- coding: utf-8 -*-
"""诊断：viewport().grab() 在本机是否能抓到真实画面（决定 3g/3h 像素判据是否有效）。"""
from __future__ import annotations
import os, sys, shutil, tempfile
from pathlib import Path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
import numpy as np
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
import rx_history, rx_store


def arr(pixmap):
    img = pixmap if isinstance(pixmap, QImage) else pixmap.toImage()
    img = img.convertToFormat(QImage.Format.Format_RGB32)
    b = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(
        img.height(), img.bytesPerLine() // 4, 4)
    return b[:, :img.width(), :3].astype(np.int16)


def render_widget(widget):
    img = QImage(widget.size(), QImage.Format.Format_RGB32)
    img.fill(0xFF000000)
    p = QPainter(img)
    widget.render(p, QPoint(0, 0))
    p.end()
    return img


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    from ui_theme import ui_font
    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))
    qs = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: qs.value(k) for k in qs.allKeys()}
    chk = Path(_ROOT) / "_check"
    hd = Path(tempfile.mkdtemp(prefix="qa_grab_hist_", dir=chk))
    dd = Path(tempfile.mkdtemp(prefix="qa_grab_db_", dir=chk))
    rr, rdb = rx_history._resolve_dir, rx_store.db_path()
    win = None
    try:
        rx_history._resolve_dir = lambda: hd
        rx_store.set_db_path(str(dd / "sessions.db"))
        import main_window
        win = main_window.MainWindow()
        win.resize(1200, 800)
        win.show(); win.raise_(); win.activateWindow()
        app.processEvents()
        win.chk_timestamp.setChecked(False)
        win._marks.clear(); win._refresh_mark_list()
        for i in range(10):
            win._emit_block(f"[2026-11-24 10:00:{i:02d}]\nVoltage1:\n"
                            f"{i+1:02d}.3800 {i+2:02d}.3820\nTemperature1:\n1.25 2.25\nBMS OK")
        app.processEvents()
        for _ in range(80):
            app.processEvents()
        bar = win.txt_rx.verticalScrollBar()
        print("bar", bar.minimum(), bar.maximum(), bar.value(), "vp", win.txt_rx.viewport().size())
        bar.setValue(0); app.processEvents()
        a1 = arr(win.txt_rx.viewport().grab())
        a1r = arr(render_widget(win.txt_rx.viewport()))
        bar.setValue(bar.maximum()); app.processEvents()
        a2 = arr(win.txt_rx.viewport().grab())
        a2r = arr(render_widget(win.txt_rx.viewport()))
        print("grab  uniform?", a1.std(), "diff(scroll)=", int((np.abs(a1 - a2).max(axis=2) > 6).sum()))
        print("render uniform?", a1r.std(), "diff(scroll)=", int((np.abs(a1r - a2r).max(axis=2) > 6).sum()))
        # 高亮：跳到第一轮
        spans = []
        d = win.txt_rx.document()
        from ui_highlight import stamp_span
        for b in range(d.blockCount()):
            if stamp_span(d.findBlockByNumber(b).text()):
                spans.append(b)
        print("stamps", spans)
        win._jump_to_mark(win._marks.marks[0].key if win._marks.marks else "")
        # 先加一条标记
        win._marks.add(spans[0], d.findBlockByNumber(spans[0]).text(), "x")
        win._refresh_mark_list()
        win._mark_list.itemClicked.emit(win._mark_list.item(0))
        app.processEvents()
        sel = win._mark_selection
        print("sel?", sel is not None, sel.cursor.selectedText()[:30] if sel else None)
        print("extraSelections len:", len(win.txt_rx.extraSelections()))
        pre = arr(win.txt_rx.viewport().grab())
        win._clear_mark_highlight(); app.processEvents()
        post = arr(win.txt_rx.viewport().grab())
        print("grab   diff(highlight)=", int((np.abs(pre - post).max(axis=2) > 6).sum()))
        pre2 = arr(render_widget(win.txt_rx.viewport()))
        win._mark_list.itemClicked.emit(win._mark_list.item(0)); app.processEvents()
        post2 = arr(render_widget(win.txt_rx.viewport()))
        print("render diff(highlight)=", int((np.abs(pre2 - post2).max(axis=2) > 6).sum()))
        # 抓个图存证
        win.txt_rx.viewport().grab().save(str(chk / "_qa_ms_grab_probe.png"))
    finally:
        if win is not None:
            win.close(); win.deleteLater()
        app.processEvents()
        rx_history._resolve_dir = rr
        rx_store.shutdown(); rx_store.set_db_path(rdb)
        for k in list(qs.allKeys()):
            if k not in snap:
                qs.remove(k)
        for k, v in snap.items():
            qs.setValue(k, v)
        qs.sync()
        shutil.rmtree(hd, ignore_errors=True); shutil.rmtree(dd, ignore_errors=True)
    app.quit()
    return 0

if __name__ == "__main__":
    sys.exit(main())
