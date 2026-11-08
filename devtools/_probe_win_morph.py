# -*- coding: utf-8 -*-
r"""验证 2026-10-31 新增的两项窗口行为：

1. 分屏（1/3、1/4 窄档）自动"最大化接收区"：窄窗里藏页头/配置卡/发送卡，
   变宽自动还原（且按进来时的可见性还原）。
2. 关闭有淡出：点关闭键后窗口不透明度在 ~160ms 内降到 0 才收起。

只读验证，不写产品代码；QSettings 全量快照 + finally 还原。全程 windows 平台。
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def pump(app, ms=120):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def main() -> int:
    app = QApplication(sys.argv)
    import ui_theme
    app.setStyle("Fusion")
    app.setFont(ui_theme.ui_font(12.5))

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    problems = []
    window = None
    scratch = None
    try:
        from main_window import MainWindow
        window = MainWindow()
        window.show()
        pump(app, 200)

        # ---- 1) 分屏自动最大化接收区 ----
        print("== 分屏自动最大化接收区 ==")
        scr = window.screen().availableGeometry()
        wide_w, wide_h = scr.width() * 3 // 4, scr.height() * 3 // 4
        print(f"  屏幕可用 {scr.width()}x{scr.height()}")
        window.move(140, 90)                       # 普通窗口：不贴边
        window.resize(wide_w, wide_h)
        pump(app, 200)
        wide = (window._snap_zoom, window.brand_header.isVisible(),
                window._config_card.isVisible(), window._tx_card.isVisible())
        print(f"  1000px（1/2 档）: snap={wide[0]} 页头={wide[1]} 配置={wide[2]} 发送={wide[3]}")
        if wide[0]:
            problems.append("1000px 不该自动最大化接收区")

        window.move(0, 0)                          # 贴左上角 = 模拟 Windows 分屏贴靠
        window.resize(scr.width() // 2, scr.height())
        pump(app, 300)
        narrow = (window._snap_zoom, window.brand_header.isVisible(),
                  window._tx_card.isVisible(), window._config_card.isVisible(),
                  window.chk_snap_power.isVisible(), window._rx_card.isVisible(),
                  bool(window.chk_snap_power.text()))
        print(f"  分屏: snap={narrow[0]} 页头={narrow[1]} 发送={narrow[2]} "
              f"配置卡={narrow[3]} 工具栏开关={narrow[4]} 接收={narrow[5]} 开关带字={narrow[6]}")
        if not narrow[0]:
            problems.append("分屏应自动最大化接收区")
        if narrow[1] or narrow[2] or narrow[3]:
            problems.append("分屏页头/发送/配置卡都应藏起来")
        if not narrow[4]:
            problems.append("分屏必须在接收区工具栏保留串口开关")
        if narrow[6]:
            problems.append("工具栏开关不该带文字")
        if not narrow[5]:
            problems.append("分屏接收卡必须可见")
        window.move(140, 90)                       # 离开边缘 = 退出分屏
        window.resize(wide_w, wide_h)
        pump(app, 300)
        back = (window._snap_zoom, window.brand_header.isVisible(),
                window._tx_card.isVisible(), window.chk_snap_power.isVisible())
        print(f"  回宽: snap={back[0]} 页头={back[1]} 发送={back[2]} 工具栏开关={back[3]}")
        if back[0]:
            problems.append("回宽后 snap 标记没清")
        if not (back[1] and back[2]):
            problems.append("回宽后卡片没还原")
        if back[3]:
            problems.append("回宽后工具栏开关应隐藏")

        # ---- 2) 关闭淡出 ----
        print("== 关闭淡出 ==")
        pump(app, 150)
        window.setWindowOpacity(1.0)
        t0 = time.monotonic()
        window._caption_bar.btn_close.click()
        trace = []
        for _ in range(60):
            app.processEvents()
            trace.append(round(window.windowOpacity(), 3))
            if not window.isVisible():
                break
            time.sleep(0.005)
        elapsed = (time.monotonic() - t0) * 1000
        print(f"  点击后不透明度轨迹 {trace[:12]}…")
        print(f"  收起耗时 {elapsed:.0f}ms，最终可见={window.isVisible()}")
        if trace and min(trace[:6]) >= 0.95:
            problems.append("关闭没有淡出（前几帧不透明度没降）")
        if window.isVisible():
            problems.append("关闭后窗口仍可见")
        window = None

        print("\n== 结论 ==")
        print("  全部通过" if not problems else "  ⛔ " + "；".join(problems))
    finally:
        if scratch is not None:
            try:
                scratch._closing = True
                scratch.close()
            except Exception:
                pass
        if window is not None:
            try:
                window._closing = True
                window.close()
            except Exception:
                pass
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            if value is None:
                settings.remove(key)
            else:
                settings.setValue(key, value)
        settings.sync()
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
