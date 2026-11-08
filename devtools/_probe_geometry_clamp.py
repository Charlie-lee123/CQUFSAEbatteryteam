# -*- coding: utf-8 -*-
r"""验证「保存的窗口几何超出屏幕时会被夹回可用区」（用户 2026-11-07 截图问题）。

模拟：把窗口设成 1060×920+0+0（用户那台 125% 缩放下可用区只有 ~1536×912），
再调 `_clamp_geometry_to_screen`，断言结果落在屏幕可用区内；另测一个正常几何
不被改动（幂等），以及一条"位置在屏幕外"的情况。
⛔ 只读探针：不改产品源码、不打包、QSettings 前后快照还原。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    from PySide6.QtCore import QRect, QSettings
    from PySide6.QtWidgets import QApplication

    import ui_theme
    _st = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: _st.value(k) for k in _st.allKeys()}

    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setStyle("Fusion")
    mode = str(_st.value("theme_mode", "system") or "system")
    ui_theme.apply_scheme(ui_theme.detect_system_scheme() if mode == "system" else mode)
    app.setFont(ui_theme.ui_font(12.5))

    from main_window import MainWindow
    win = MainWindow()
    win._skip_startup_anim = True
    scr = win.screen() or app.primaryScreen()
    avail = scr.availableGeometry()
    print(f"屏幕可用区 = {avail.width()}x{avail.height()}+{avail.x()}+{avail.y()}")

    cases = [
        ("超出高度（模拟用户那台 125% 缩放）",
         QRect(avail.x(), avail.y(), 1060, avail.height() + 60)),
        ("超出宽度", QRect(avail.x(), avail.y(), avail.width() + 400, 600)),
        ("位置在屏幕右下外", QRect(avail.right() + 300, avail.bottom() + 300, 900, 700)),
        ("正常几何（应原样不动）", QRect(avail.x() + 40, avail.y() + 40, 1000, 700)),
    ]
    ok = True
    for name, rect in cases:
        win.setGeometry(rect)
        before = win.geometry()
        win._clamp_geometry_to_screen()
        g = win.geometry()
        inside = (g.width() <= avail.width() and g.height() <= avail.height()
                  and g.x() >= avail.left() and g.y() >= avail.top()
                  and g.right() <= avail.right() and g.bottom() <= avail.bottom())
        unchanged = (g == before)
        expect_unchanged = name.startswith("正常")
        good = inside and (unchanged == expect_unchanged)
        ok = ok and good
        print(f"  [{'OK' if good else 'FAIL'}] {name}: "
              f"{before.width()}x{before.height()}+{before.x()}+{before.y()} → "
              f"{g.width()}x{g.height()}+{g.x()}+{g.y()}"
              f"{'（未改动）' if unchanged else ''}")

    win.close()
    st2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st2.allKeys()) - set(snap):
        st2.remove(k)
    for k, v in snap.items():
        st2.setValue(k, v)
    st2.sync()
    print("结论：" + ("全部 OK" if ok else "有 FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
