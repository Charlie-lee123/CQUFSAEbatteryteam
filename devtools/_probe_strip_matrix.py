"""复现「接收区全屏时上方出现 1~2px 小条」并按动作序列定位是哪一步留下的。

为什么之前 12 个场景没复现：探针用的是**逐帧差分**（PrintWindow vs BitBlt、
frame N vs N+1）。这条小条是**静止**的 —— 一旦出现就一直钉在同一行像素上，
差分里它前后两帧都有、相减为零，天生看不见。这里改成**绝对外观**判据：
看 `_rx_opts_row` / `_opts_content` 的终态高度，以及全屏态下这一行是否仍然
`isVisible()` 且高度落在 (0, 18) 这个"只见一条缝"的区间。

用法：python devtools/_probe_strip_matrix.py
输出：_check/strip_matrix.txt（同时打到 stdout）

⛔ 全程串行、独占 QSettings：进程序快照 → 结束时无条件全量还原。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "_check"

from PySide6.QtCore import QEvent, QPoint, QSettings  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main_window as mw  # noqa: E402
from ui_theme import ui_font  # noqa: E402


def _payload(i: int) -> bytes:
    cells = " ".join(f"{3300 + (i * 7 + k) % 60}" for k in range(12))
    temps = " ".join(f"{24 + (i + k) % 8}" for k in range(12))
    return (f"Voltage1: {cells}\nTemperature1: {temps}\n").encode()


def _state(w) -> dict:
    row, content = w._rx_opts_row, w._opts_content
    try:
        rx_top = w.txt_rx.mapTo(w, QPoint(0, 0)).y()
    except RuntimeError:
        rx_top = -1
    return {
        "row_h": row.height(), "row_vis": row.isVisible(),
        "row_min": row.minimumHeight(), "row_max": row.maximumHeight(),
        "c_h": content.height(), "c_min": content.minimumHeight(),
        "c_max": content.maximumHeight(),
        "rx_top": rx_top, "exp": w._expanded, "t": round(w._rx_t, 3),
        "paused": w._paused,
    }


def _fmt(s: dict) -> str:
    mx = "INF" if s["row_max"] >= 99999 else s["row_max"]
    cmx = "INF" if s["c_max"] >= 99999 else s["c_max"]
    return (f"row(h={s['row_h']:3d} vis={int(s['row_vis'])} min={s['row_min']:2d} max={mx}) "
            f"cont(h={s['c_h']:3d} min={s['c_min']:2d} max={cmx}) "
            f"rx_top={s['rx_top']:4d} exp={int(s['exp'])} t={s['t']:.2f} "
            f"paused={int(s['paused'])}")


def _bad(s: dict) -> str:
    """全屏态下「只见一条缝」的判据。返回非空字符串表示中招。"""
    if not s["exp"]:
        return ""
    if s["row_vis"] and 0 < s["row_h"] < 18:
        return f"行可见且只有 {s['row_h']}px"
    if 0 < s["c_h"] < 18:
        return f"内容被钉成 {s['c_h']}px"
    return ""


def _wait(app, ms: int = 420) -> None:
    app.processEvents()
    QTest.qWait(ms)
    app.processEvents()


def _note(app, w, tag: str) -> str:
    s = _state(w)
    why = _bad(s)
    mark = f"HIT({why}) " if why else ""
    return f"    {mark}{tag}: {_fmt(s)}"


def _feed(app, w, count: int, gap: int = 40, tag: str = "stream") -> str:
    """喂数据，每 6 帧采一次；发现坏态立刻记下。返回最后一行日志。"""
    hits: list[str] = []
    for i in range(count):
        w._on_received(_payload(i))
        if i % 6 == 5:
            app.processEvents()
            QTest.qWait(gap)
            s = _state(w)
            why = _bad(s)
            if why:
                hits.append(f"    HIT 第 {i + 1} 帧：{why} | {_fmt(s)}")
    _wait(app, 320)
    s = _state(w)
    why = _bad(s)
    tail = f"    {'HIT(' + why + ') ' if why else ''}{tag} 终态: {_fmt(s)}"
    body = ("\n".join(hits[:3]) + "\n") if hits else ""
    return body + tail


def _run(app, tag: str, steps) -> str:
    w = mw.MainWindow()
    w.show()
    _wait(app, 500)
    lines = []
    try:
        for fn in steps:
            lines.append(fn(app, w))
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    return f"  {tag}\n" + "\n".join(lines)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    def s_expand(a, w):
        w._toggle_expand()
        _wait(a, 900)
        return _note(a, w, "进全屏")

    def s_stream(a, w):
        return _feed(a, w, 90)

    def s_pause(a, w):
        w._toggle_pause()
        _wait(a, 600)
        return _note(a, w, "暂停")

    def s_resume(a, w):
        w._toggle_pause()
        _wait(a, 600)
        return _note(a, w, "恢复")

    def s_collapse(a, w):
        w._toggle_expand()
        _wait(a, 900)
        return _note(a, w, "退全屏")

    def s_search_on(a, w):
        w._toggle_search()
        _wait(a, 500)
        return _note(a, w, "搜索条开")

    def s_search_off(a, w):
        w._close_search()
        _wait(a, 500)
        return _note(a, w, "搜索条关")

    def s_font15(a, w):
        w._apply_rx_font_size(15)
        _wait(a, 500)
        return _note(a, w, "字号15")

    def s_font11(a, w):
        w._apply_rx_font_size(11)
        _wait(a, 500)
        return _note(a, w, "字号11")

    def s_normal(a, w):
        w.showNormal()
        _wait(a, 700)
        return _note(a, w, "窗口还原")

    def s_full(a, w):
        w.showFullScreen()
        _wait(a, 700)
        return _note(a, w, "再全屏")

    def s_triple(a, w):
        w._toggle_expand()
        QTest.qWait(90)
        w._toggle_expand()
        QTest.qWait(90)
        w._toggle_expand()
        _wait(a, 1200)
        return _note(a, w, "连点三次")

    def s_stream40(a, w):
        return _feed(a, w, 40)

    def s_stream30(a, w):
        return _feed(a, w, 30)

    scenarios = [
        ("S1 全屏 → 流式", [s_expand, s_stream]),
        ("S2 暂停 → 全屏 → 恢复 → 流式",
         [s_pause, s_expand, s_resume, s_stream40]),
        ("S3 全屏 → 暂停 → 恢复 → 流式", [s_expand, s_pause, s_resume, s_stream40]),
        ("S4 全屏 → 退全屏 → 暂停 → 全屏 → 流式",
         [s_expand, s_collapse, s_pause, s_expand, s_stream40]),
        ("S5 全屏 → 流式 → 搜索条开关 → 流式",
         [s_expand, s_stream30, s_search_on, s_search_off, s_stream40]),
        ("S6 全屏 → 流式 → 改字号 → 流式",
         [s_expand, s_stream30, s_font15, s_font11, s_stream40]),
        ("S7 全屏 → 流式 → 窗口还原 → 再全屏 → 流式",
         [s_expand, s_stream30, s_normal, s_full, s_stream40]),
        ("S8 全屏连点三次 → 流式", [s_triple, s_stream40]),
    ]

    report: list[str] = []
    for tag, steps in scenarios:
        try:
            report.append(_run(app, tag, steps))
        except Exception as exc:  # noqa: BLE001
            report.append(f"  ✗ {tag} 抛异常 {type(exc).__name__}: {exc}")

    settings = QSettings("MimoWorks", "SerialAssistant")
    for key in settings.allKeys():
        if key not in snapshot:
            settings.remove(key)
    for key, value in snapshot.items():
        settings.setValue(key, value)
    settings.sync()

    text = "\n".join(report)
    OUT.mkdir(exist_ok=True)
    (OUT / "strip_matrix.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
