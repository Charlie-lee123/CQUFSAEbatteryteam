"""阳性对照：证明"全屏下 1~2px 小条"就是旧版 _rx_capture_full 把 opts_h 量成 1 造成的。

做法：同一台机器、同一条动作序列（普通态暂停 → 进全屏 → 恢复滚动），跑两遍：
  A. **旧逻辑**：量 ``max(1, row.height())`` 并无条件 ``setFixedHeight``，
     恢复时无条件 ``_animate_opts_row(True)``（= 老代码）；
  B. **新逻辑**：现在的 ``_rx_capture_full`` + ``_sync_opts_row``。
判据：A 必须复现"行可见且高度 < 18"，B 必须保持隐藏（0）。

用法：python devtools/_probe_strip_repro.py
输出：_check/strip_repro.txt
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


def _state(w) -> str:
    row, c = w._rx_opts_row, w._opts_content
    mx = "INF" if row.maximumHeight() >= 99999 else row.maximumHeight()
    cmx = "INF" if c.maximumHeight() >= 99999 else c.maximumHeight()
    top = w.txt_rx.mapTo(w, QPoint(0, 0)).y()
    return (f"row(h={row.height():3d} vis={int(row.isVisible())} max={mx}) "
            f"cont(h={c.height():3d} max={cmx}) rx_top={top}")


def _bad(w) -> str:
    row, c = w._rx_opts_row, w._opts_content
    if row.isVisible() and 0 < row.height() < 18:
        return f"✗ 复现：行可见且只有 {row.height()}px（就是那条小条）"
    if 0 < c.height() < 18:
        return f"✗ 复现：内容被钉成 {c.height()}px"
    return "✓ 没有小条"


def _wait(app, ms: int) -> None:
    app.processEvents()
    QTest.qWait(ms)
    app.processEvents()


def _install_old_behaviour(w) -> None:
    """把窗口降级成"老代码"：量 height() 且无条件钉住、恢复时无条件展开。"""
    orig_capture = w._rx_capture_full

    def old_capture() -> None:
        orig_capture()
        w._rx_full["opts_h"] = max(1, w._rx_opts_row.height())
        w._opts_content.setFixedHeight(w._rx_full["opts_h"])

    w._rx_capture_full = old_capture          # type: ignore[method-assign]
    w._heal_opts_content = lambda: None        # 老代码没有自愈


def _run(app, old: bool) -> str:
    w = mw.MainWindow()
    if old:
        _install_old_behaviour(w)
    w.show()
    _wait(app, 500)
    log = [f"    起手: {_state(w)}"]
    # ① 普通态暂停
    w._toggle_pause()
    _wait(app, 450)
    log.append(f"    暂停: {_state(w)}")
    # ② 进全屏
    w._toggle_expand()
    _wait(app, 900)
    log.append(f"    全屏: {_state(w)}")
    # ③ 恢复滚动
    if old:
        # 老代码：旗子在自己身上，恢复时无条件展开
        w._paused = False
        w._refresh_pause_ui()
        w._animate_opts_row(True)
        _wait(app, 600)
    else:
        w._toggle_pause()
        _wait(app, 600)
    log.append(f"    恢复: {_state(w)}")
    # ④ 流式（滚起来才会被用户看见）
    for i in range(24):
        w._on_received(f"Voltage1: {3300 + i}\nTemperature1: {25}\n".encode())
        if i % 6 == 5:
            QTest.qWait(40)
            app.processEvents()
    _wait(app, 400)
    log.append(f"    流式后: {_state(w)}")
    verdict = _bad(w)
    w.close()
    w.deleteLater()
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    return "\n".join(log) + f"\n    判据: {verdict}"


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    report = []
    try:
        report.append("=== A. 旧逻辑（对照）===")
        report.append(_run(app, old=True))
        report.append("\n=== B. 新逻辑（修复后）===")
        report.append(_run(app, old=False))
    finally:
        settings = QSettings("MimoWorks", "SerialAssistant")
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()

    text = "\n".join(report)
    OUT.mkdir(exist_ok=True)
    (OUT / "strip_repro.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
