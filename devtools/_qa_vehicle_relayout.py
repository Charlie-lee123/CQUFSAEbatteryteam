# -*- coding: utf-8 -*-
r"""QA 独立探针：设置面板改车辆 → 主窗口接收区**真的**跟着重排（200ms 合并）。

验两件事：
  1. 选车后 `_preview_custom("vehicle", …)` 能把已显示的接收区按新结构重排
     （74 点：没选车时一整排 → 选车后 12/13/12/12/12/13）；
  2. 连发 5 次通知只重排 **1 次**（200ms 合并窗口生效，不是每次点击排一遍）。

用法：QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_qa_vehicle_relayout.py
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings                              # noqa: E402
from PySide6.QtWidgets import QApplication                        # noqa: E402

FAILS: list[str] = []


def pump(app, ms: int) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.003)


def row_counts(window) -> list[int]:
    from ui_highlight import SAMPLE_RE
    document = window.txt_rx.document()
    out = []
    for i in range(document.blockCount()):
        line = document.findBlockByNumber(i).text()
        n = len(SAMPLE_RE.findall(line))
        if n:
            out.append(n)
    return out


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, ui_font
    apply_scheme("dark")
    app.setFont(ui_font(12.5))

    import customization
    from selftest import restore_settings

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    window = None
    try:
        for name in customization.vehicle_names():
            customization.delete_vehicle(name)
        customization.select_vehicle("")

        from main_window import MainWindow
        window = MainWindow()
        window._port_watch.stop()
        window.setGeometry(0, 0, 1600, 952)
        window.show()
        pump(app, 700)
        window._want_fullscreen = False
        window._expect_normal = True
        window.showNormal()
        window.setGeometry(0, 0, 1600, 952)
        pump(app, 600)

        # ---- 1) 没选车：老排版（一整段）----
        window._clear_receive()
        window._on_received(
            ("Voltage1:\n" + " ".join(f"{i}.3700" for i in range(1, 75))).encode())
        window._flush_rx()
        pump(app, 500)
        before = row_counts(window)
        print("没选车时每排点数 =", before, " 合计", sum(before))

        # ---- 包一层计数，看 200ms 合并 ----
        calls = {"n": 0}
        original = window._relayout_vehicle_grid

        def counted():
            calls["n"] += 1
            original()

        window._relayout_vehicle_grid = counted

        customization.save_vehicle("接线验证车", [25, 24, 25], [8, 5, 6])
        customization.select_vehicle("接线验证车")
        for _ in range(5):                       # 模拟连点 5 次
            window._preview_custom("vehicle", "接线验证车")
            pump(app, 30)
        print("  5 次通知、30ms 间隔 → 计时器已排队，已重排次数 =", calls["n"])
        pump(app, 500)
        after = row_counts(window)
        print("选车后每排点数 =", after, " 合计", sum(after))
        print("  合并后实际重排次数 =", calls["n"])

        if calls["n"] != 1:
            FAILS.append(f"200ms 合并失效：5 次通知触发了 {calls['n']} 次重排")
        if after != [12, 13, 12, 12, 12, 13]:
            FAILS.append(f"选车后接收区没按逐模组重排：{after}")
        if sum(after) != 74:
            FAILS.append(f"重排后点数不对：{sum(after)}")
        if before == after:
            FAILS.append("选车前后排版完全一样，重排没有真正发生")
    finally:
        if window is not None:
            window.setGeometry(0, 0, 1134, 820)
            window.close()
            app.processEvents()
        pump(app, 150)
        restore_settings(settings, snapshot)
        settings.sync()

    print("\n================ QA 判定 ================")
    if FAILS:
        for item in FAILS:
            print("FAIL  " + item)
        return 1
    print("PASS  选车后接收区按逐模组重排，且 200ms 内多次通知只排一次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
