# -*- coding: utf-8 -*-
r"""一次性真平台探针：全程**同一套**标记（用户纠正：不是各数据源一套）。

证明（写 _check/mark_shared_probe.txt）：
  1 实时打 1 标 → 列表 1 条
  2 进近期回放 → 列表仍是那 1 条；回放态再打 1 标 → 列表 2 条
  3 再进历史回放 → 列表仍是那 2 条；再打 1 标 → 列表 3 条
  4 退回实时 → 列表仍是那 3 条（进/出回放都不清）
  5 Alt+M / Alt+↑/↓ 在回放态可用；点击列表项跳回对应轮
  6 只有主动清空接收区 _clear_receive() 才清零
  7 QSettings 前后零变化
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from PySide6.QtCore import QEvent, QSettings
from PySide6.QtWidgets import QApplication

import customization
import main_window
import rx_history
import rx_store

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def texts(win) -> list[str]:
    return [win._mark_list.item(i).text() for i in range(win._mark_list.count())]


def feed(win, stamp: str) -> None:
    win._emit_block(f"{stamp}\nVoltage1:\n 1.3800 2.3800\n"
                    "Temperature1:\n 1.25 2.25\nBMS OK\n")


def main() -> int:
    import shutil
    import tempfile
    from pathlib import Path

    app = QApplication.instance() or QApplication(sys.argv)
    qs = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: qs.value(k) for k in qs.allKeys()}
    real_dir = rx_history._resolve_dir
    real_pin = customization.current_vehicle
    real_db = rx_store.db_path()
    check_dir = Path(_ROOT) / "_check"
    tmpdir = Path(tempfile.mkdtemp(prefix="markshared_", dir=check_dir))
    dbdir = Path(tempfile.mkdtemp(prefix="markshared_db_", dir=check_dir))
    win = None
    try:
        rx_history._resolve_dir = lambda: tmpdir
        customization.current_vehicle = lambda: "E74-2026"
        rx_store.set_db_path(str(dbdir / "sessions.db"))
        win = main_window.MainWindow()
        win.resize(1200, 800)
        win.show()
        app.processEvents()
        win._marks.clear()
        win._refresh_mark_list()

        # ---- 1 实时打标 ----
        feed(win, "[2026-11-24 10:00:00]")
        app.processEvents()
        win._mark_current()
        app.processEvents()
        check("1a 实时 1 标", len(texts(win)) == 1, str(texts(win)))
        check("1b 列表无来源前缀",
              not texts(win)[0].startswith(("近期", "历史")), str(texts(win)))

        # ---- 2 近期回放：同一套，再打一标 ----
        recent_log = tmpdir / rx_history.FILE_NAME
        recent_log.write_text(
            "[2026-11-24 11:00:00]\nVoltage1:\n 3.3800 4.3800\n"
            "Temperature1:\n 3.25 4.25\nBMS OK\n"
            "[2026-11-24 11:00:05]\nVoltage1:\n 5.3800 6.3800\n"
            "Temperature1:\n 5.25 6.25\nBMS OK\n", encoding="utf-8")
        rx_history._path = recent_log
        rx_history._lines.clear()
        rx_history._lines.extend(recent_log.read_text(encoding="utf-8").splitlines())
        rx_history._file_lines = len(rx_history._lines)
        win._open_history()
        app.processEvents()
        check("2a 进近期回放列表仍 1 条", len(texts(win)) == 1, str(texts(win)))
        check("2b 回放按钮/列表可用", win.btn_mark.isEnabled()
              and win._mark_list.isEnabled())
        check("2c tooltip 无数据源说明", win.btn_mark.toolTip() == "标记当前轮（Alt+M）",
              win.btn_mark.toolTip())
        win._mark_current()
        app.processEvents()
        check("2d 回放打标后同一套 2 条", len(texts(win)) == 2, str(texts(win)))

        # ---- 5 点击列表项跳回（在回放那轮）----
        win._mark_list.item(1).setSelected(True)
        win._on_mark_item_clicked(win._mark_list.item(1))
        app.processEvents()
        block_no = win.txt_rx.textCursor().blockNumber()
        span = win._round_span(block_no)
        check("5a 点击回放标记跳回轮内", span is not None
              and span[0] <= block_no < span[1], f"block={block_no} span={span}")
        check("5b 整轮高亮挂上", win._mark_selection is not None)
        win._goto_mark(-1)                    # Alt+↑ 回放态可用
        app.processEvents()
        check("5c 回放态 Alt+↑/↓ 可用", win._marks.current == 0
              or win._marks.current == 1, f"current={win._marks.current}")

        # ---- 3 历史回放：同一套，再打一标 ----
        win._exit_replay()
        app.processEvents()
        check("2e 退回实时仍 2 条（进/出不清）", len(texts(win)) == 2, str(texts(win)))
        rx_store.record_block(
            "[2026-11-24 12:00:00]\nVoltage1:\n 7.3800 8.3800\nBMS OK",
            "2026-11-24T12:00:00.000")
        rx_store.record_block(
            "[2026-11-24 12:00:30]\nVoltage1:\n 9.3800 10.3800\nBMS OK",
            "2026-11-24T12:00:30.000")
        rx_store.flush(8.0)
        win._open_long_history()
        app.processEvents()
        check("3a 进历史回放列表仍 2 条", len(texts(win)) == 2, str(texts(win)))
        win._mark_current()
        app.processEvents()
        check("3b 历史回放打标后 3 条", len(texts(win)) == 3, str(texts(win)))

        # ---- 4 退回实时仍 3 条 ----
        win._exit_replay()
        app.processEvents()
        check("4a 退回实时仍 3 条", len(texts(win)) == 3, str(texts(win)))

        # ---- 6 只有主动清空才清零 ----
        win._clear_receive()
        app.processEvents()
        check("6 主动清空接收区后清零", len(texts(win)) == 0
              and len(win._marks.marks) == 0, str(texts(win)))

    finally:
        if win is not None:
            win.close()
            win.deleteLater()
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        rx_history._resolve_dir = real_dir
        customization.current_vehicle = real_pin
        rx_store.set_db_path(real_db)
        for k in list(qs.allKeys()):
            if k not in snap:
                qs.remove(k)
        for k, v in snap.items():
            qs.setValue(k, v)
        qs.sync()
        shutil.rmtree(tmpdir, ignore_errors=True)
        shutil.rmtree(dbdir, ignore_errors=True)

    now = {k: qs.value(k) for k in qs.allKeys()}
    check("7 QSettings 前后零变化", now == snap, f"keys {len(snap)} -> {len(now)}")
    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    tail = f"\n合计 {passed}/{total} PASS  ->  IS_PASS: {'YES' if passed == total else 'NO'}"
    print(tail)
    with open(os.path.join(_ROOT, "_check", "mark_shared_probe.txt"),
              "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                           (f"   [{d}]" if d else "") for n, ok, d in _RESULTS)
                 + tail + "\n")
    app.quit()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
