# -*- coding: utf-8 -*-
r"""一次性真平台探针：「查看标记」按钮 + 弹窗列表（v2.11 标记 UI 改动）。

证明（写 _check/marks_btn_probe.txt）：
  A 卡片里无常驻 QListWidget（_mark_list 平时 None）、有 btn_marks 且在 _config_content 内
  B 空标记点按钮 → 只 toast、不弹窗（_mark_list 仍 None）
  C 打 3 条标 → 点按钮弹窗、列表 3 条；点第 2 条 → 主窗落第 2 轮 + 弹窗关闭；再开仍 3 条
  D 清空接收区 → 再点按钮 → 不弹窗 + toast（数据空）
  E 回放态点按钮可正常打开、点项能跳
  F QSettings 前后零变化
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from PySide6.QtCore import QEvent, QSettings
from PySide6.QtWidgets import QApplication

import customization
import main_window
import rx_history

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def in_chain(widget, ancestor) -> bool:
    cur = widget
    while cur is not None:
        if cur is ancestor:
            return True
        cur = cur.parentWidget()
    return False


def token_of(win, span) -> str:
    d = win.txt_rx.document()
    for b in range(span[0] + 1, span[1]):
        t = d.findBlockByNumber(b).text().strip()
        if "TOKEN_" in t:
            return t
    return ""


def highlighted_token(win) -> str:
    sel = win._mark_selection
    if sel is None:
        return ""
    pos = sel.cursor.selectionStart()
    bn = win.txt_rx.document().findBlock(pos).blockNumber()
    span = win._round_span(bn)
    return "" if span is None else token_of(win, span)


def round_text(stamp: str, token: str) -> str:
    return f"{stamp}\nVoltage1:\n 1.3800\n{token}\nBMS OK\n"


def main() -> int:
    import shutil
    import tempfile
    from pathlib import Path

    app = QApplication.instance() or QApplication(sys.argv)
    qs = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: qs.value(k) for k in qs.allKeys()}
    real_dir = rx_history._resolve_dir
    real_pin = customization.current_vehicle
    check_dir = Path(_ROOT) / "_check"
    tmpdir = Path(tempfile.mkdtemp(prefix="marksbtn_", dir=check_dir))
    win = None
    try:
        rx_history._resolve_dir = lambda: tmpdir
        customization.current_vehicle = lambda: "E74-2026"
        win = main_window.MainWindow()
        win.resize(1200, 800)
        win.show()
        app.processEvents()
        win._marks.clear()

        # ---- A 卡片里无常驻列表、有 btn_marks 且在 _config_content 内 ----
        check("A1 初始 _mark_list 为 None（不常驻卡片）",
              win._mark_list is None, str(win._mark_list))
        check("A2 有 btn_marks", getattr(win, "btn_marks", None) is not None)
        check("A3 btn_marks 文案=查看标记",
              win.btn_marks.text() == "查看标记", win.btn_marks.text())
        check("A4 btn_marks 在 _config_content 内",
              in_chain(win.btn_marks, win._config_content),
              f"parent={win.btn_marks.parentWidget()}")

        # ---- B 空标记点按钮 → 只 toast 不弹窗 ----
        toasts: list[str] = []
        _orig = win._toast
        win._toast = lambda msg, ms=2000: (toasts.append(msg), _orig(msg, ms))
        win._show_marks_dialog()
        app.processEvents()
        check("B1 空标记不弹窗（_mark_list 仍 None）", win._mark_list is None)
        check("B2 空标记有 toast", any("还没有标记" in t for t in toasts), str(toasts))

        # ---- C 打 3 条标 → 弹窗列 3 → 点第 2 条跳第 2 轮 + 关窗；再开仍 3 ----
        win._clear_receive()
        app.processEvents()
        for i, tok in enumerate(("TOKEN_M1", "TOKEN_M2", "TOKEN_M3")):
            win._emit_block(round_text(f"[2026-11-24 12:00:0{i}]", tok))
        app.processEvents()
        win._marks.clear()
        d = win.txt_rx.document()
        stamps = [b for b in range(d.blockCount())
                  if "2026-11-24 12:00:0" in d.findBlockByNumber(b).text()]
        for blk in stamps:
            sp = win._round_span(blk)
            sg = win._round_content_sig(sp)
            rv = sum(1 for b in range(blk + 1, d.blockCount())
                     if d.findBlockByNumber(b).text() == d.findBlockByNumber(blk).text())
            win._marks.add_unique(blk, d.findBlockByNumber(blk).text(),
                                  token_of(win, sp), rv, sg)
        win._refresh_mark_list()
        check("C0 已打 3 条标", len(win._marks.marks) == 3,
              f"count={len(win._marks.marks)}")

        win._show_marks_dialog()
        app.processEvents()
        check("C1 弹窗打开且有 _mark_list", win._mark_list is not None)
        check("C2 弹窗列表 3 条", win._mark_list.count() == 3,
              f"count={win._mark_list.count()}")
        # 点第 2 条 → 跳第 2 轮 + 关窗
        win._mark_list.item(1).setSelected(True)
        win._on_mark_item_clicked(win._mark_list.item(1))
        app.processEvents()
        check("C3 点第 2 条后弹窗关闭", not win._marks_dialog.isVisible())
        got = highlighted_token(win)
        check("C4 主窗整轮高亮落第 2 条对应轮(TOKEN_M2)", got == "TOKEN_M2",
              f"got={got!r}")
        # 再开仍 3 条（同一份 MarkStore）
        win._show_marks_dialog()
        app.processEvents()
        check("C5 再次打开列表仍 3 条", win._mark_list.count() == 3,
              f"count={win._mark_list.count()}")
        win._marks_dialog.accept()
        app.processEvents()

        # ---- D 清空接收区 → 再点按钮 → 不弹窗 + toast ----
        win._clear_receive()
        app.processEvents()
        toasts.clear()
        _prev_list = win._mark_list
        win._show_marks_dialog()
        app.processEvents()
        check("D1 清空后不弹窗（数据空）", len(win._marks.marks) == 0
              and any("还没有标记" in t for t in toasts), str(toasts))

        # ---- E 回放态点按钮可正常打开、点项能跳 ----
        # 造近期数据并进回放
        recent = tmpdir / rx_history.FILE_NAME
        recent.write_text(round_text("[2026-11-24 13:00:00]", "TOKEN_RP1")
                          + round_text("[2026-11-24 13:00:05]", "TOKEN_RP2"),
                          encoding="utf-8")
        rx_history._path = recent
        rx_history._lines.clear()
        rx_history._lines.extend(recent.read_text(encoding="utf-8").splitlines())
        rx_history._file_lines = len(rx_history._lines)
        win._open_history()
        app.processEvents()
        # 回放里打 2 条
        win._marks.clear()
        d = win.txt_rx.document()
        stp = [b for b in range(d.blockCount())
               if "2026-11-24 13:00:0" in d.findBlockByNumber(b).text()]
        for blk in stp:
            sp = win._round_span(blk)
            sg = win._round_content_sig(sp)
            rv = sum(1 for b in range(blk + 1, d.blockCount())
                     if d.findBlockByNumber(b).text() == d.findBlockByNumber(blk).text())
            win._marks.add_unique(blk, d.findBlockByNumber(blk).text(),
                                  token_of(win, sp), rv, sg)
        win._refresh_mark_list()
        check("E0 回放态 btn_marks 仍 enabled", win.btn_marks.isEnabled())
        win._show_marks_dialog()
        app.processEvents()
        check("E1 回放态弹窗可打开且列 2 条",
              win._mark_list is not None and win._mark_list.count() == 2,
              f"count={win._mark_list.count() if win._mark_list else None}")
        win._mark_list.item(0).setSelected(True)
        win._on_mark_item_clicked(win._mark_list.item(0))
        app.processEvents()
        check("E2 回放态点项能跳（整轮高亮命中）", win._mark_selection is not None)
        win._exit_replay()
        app.processEvents()

    finally:
        if win is not None:
            if getattr(win, "_marks_dialog", None) is not None:
                win._marks_dialog.accept()
            win.close()
            # close() 只起 160ms 淡出；立刻 deleteLater() 会在扫描线程还活着时
            # 析构主窗 → 退出码 127、stdout 缓冲全丢（报告虽然写了但门禁看退出码）。
            for _ in range(80):
                app.processEvents()
                time.sleep(0.006)
            win.deleteLater()
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        rx_history._resolve_dir = real_dir
        customization.current_vehicle = real_pin
        for k in list(qs.allKeys()):
            if k not in snap:
                qs.remove(k)
        for k, v in snap.items():
            qs.setValue(k, v)
        qs.sync()
        shutil.rmtree(tmpdir, ignore_errors=True)

    now = {k: qs.value(k) for k in qs.allKeys()}
    check("F QSettings 前后零变化", now == snap, f"keys {len(snap)} -> {len(now)}")
    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    tail = f"\n合计 {passed}/{total} PASS  ->  IS_PASS: {'YES' if passed == total else 'NO'}"
    print(tail)
    with open(os.path.join(_ROOT, "_check", "marks_btn_probe.txt"),
              "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                           (f"   [{d}]" if d else "") for n, ok, d in _RESULTS)
                 + tail + "\n")
    app.quit()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
