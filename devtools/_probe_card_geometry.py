# -*- coding: utf-8 -*-
r"""卡片几何体检：多种窗口尺寸下量「接收区 / 串口配置」卡片的矩形与右边距。

背景
----
用户 2026-11-07 截图：「这个卡片位置怎么有点奇怪啊……要适应所有机型屏幕啊」。
截图里右侧「串口配置」卡片看起来**贴住窗口右缘、且比设计宽度窄**。
本探针按保存的几何开窗（默认 1134×820），再遍历一组典型尺寸/分屏宽度，
打印每档的 compact 级别 + 两张卡片的实际矩形 + 窗口右/下留白，并抓一张 PNG。

⛔ 只读探针：不改产品源码、不打包、不写 QSettings（前后快照对比）。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SHOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "_check", "card_geometry.png")

# 典型窗口尺寸：默认保存值、1080p@125% 最大化、1/2 分屏、1/3、1/4、极小
SIZES = [(1134, 820), (1536, 864), (960, 1040), (640, 1040), (480, 1040),
         (400, 700), (1280, 720)]


def main() -> int:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    import ui_theme
    from ui_theme import apply_scheme, ui_font

    _st = QSettings("MimoWorks", "SerialAssistant")
    _snap = {k: _st.value(k) for k in _st.allKeys()}

    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setStyle("Fusion")
    mode = str(_st.value("theme_mode", "system") or "system")
    scheme = ui_theme.detect_system_scheme() if mode == "system" else mode
    apply_scheme(scheme)
    app.setFont(ui_font(12.5))

    from main_window import MainWindow
    win = MainWindow()
    win._skip_startup_anim = True

    print(f"{'窗口':>12} {'档':>3} {'接收区卡片':>26} {'配置卡':>26} "
          f"{'右留白':>6} {'下留白':>6} {'表单宽':>6}")
    for w, h in SIZES:
        win.resize(w, h)
        win.show()
        app.processEvents()
        win.layout().activate()
        app.processEvents()
        lvl = getattr(win, "_compact_level", "?")
        rx = win._rx_card.geometry()
        cf = win._config_card.geometry()
        # 卡片 → 窗口坐标
        rx_tl = win._rx_card.mapTo(win, rx.topLeft() - rx.topLeft())
        cf_tl = win._config_card.mapTo(win, cf.topLeft() - cf.topLeft())
        right_gap = win.width() - (cf_tl.x() + cf.width())
        # 底部：发送区卡片的下缘
        tx = win._tx_card.geometry()
        tx_tl = win._tx_card.mapTo(win, tx.topLeft() - tx.topLeft())
        bottom_gap = win.height() - (tx_tl.y() + tx.height())
        content_w = win._config_content.width()
        print(f"{w}x{h:>5} {lvl:>3} "
              f"({rx_tl.x()},{rx_tl.y()}) {rx.width()}x{rx.height()}"
              f"   ({cf_tl.x()},{cf_tl.y()}) {cf.width()}x{cf.height()}"
              f"   {right_gap:>6} {bottom_gap:>6} {content_w:>6}")
        if (w, h) == SIZES[0]:
            win.grab().save(SHOT)
            print(f"  ↳ 已抓图 {SHOT}")

    win.close()
    st2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st2.allKeys()) - set(_snap):
        st2.remove(k)
    for k, v in _snap.items():
        st2.setValue(k, v)
    st2.sync()
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
