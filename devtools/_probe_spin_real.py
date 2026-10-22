# -*- coding: utf-8 -*-
"""在**真实主窗路径**里量「模组个数」数字框的上下留白。

⛔ 会开主窗口 → 跑完必须还原 geometry / custom/startup_maximized（见 finally）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main_window  # noqa: E402


def ink_rows(img, bg, x0, x1):
    def ink(c):
        return abs(c.red() - bg.red()) + abs(c.green() - bg.green()) \
            + abs(c.blue() - bg.blue()) > 60
    rows = []
    for y in range(img.height()):
        rows.append(sum(1 for x in range(x0, x1) if ink(img.pixelColor(x, y))))
    return rows


def measure(spin, tag=""):
    img = spin.grab().toImage()
    w, h = img.width(), img.height()
    rows = ink_rows(img, img.pixelColor(int(w * 0.5), h // 2), 40, int(w * 0.55))
    # 描边 = 计数极大的行（整行都是描边）；字 = 中间那些小计数行
    border_rows = [y for y, n in enumerate(rows) if n > (int(w * 0.55) - 40) * 0.6]
    body = [y for y, n in enumerate(rows)
            if 0 < n <= (int(w * 0.55) - 40) * 0.6 and y not in border_rows]
    print(f"{tag} 图像 {w}x{h} dpr={img.devicePixelRatio():.1f} "
          f"描边行={border_rows[:2]}..{border_rows[-2:] if border_rows else []}")
    if border_rows and body:
        top_b, bot_b = border_rows[0], border_rows[-1]
        print(f"{tag} 字墨迹 {body[0]}..{body[-1]}  "
              f"上留白 {body[0] - top_b}  下留白 {bot_b - body[-1]}  (物理px)")


def main() -> None:
    settings = QSettings("MimoWorks", "SerialAssistant")
    saved_geo = settings.value("geometry")
    saved_max = settings.value("custom/startup_maximized")
    settings.setValue("custom/startup_maximized", False)
    settings.sync()
    app = QApplication(sys.argv)
    # 主程序在 main.py 里干的这两件事必须照做，否则量出来的不是产品的样子
    import ui_theme as _ut
    app.setStyle("Fusion")
    app.setFont(_ut.ui_font(12.5))
    window = main_window.MainWindow()
    window.show()
    try:
        # 直接调（不进事件循环，避免 QTimer 依赖）：先让主窗布局稳定
        for _ in range(80):
            app.processEvents()
        window._open_settings()
        for _ in range(120):
            app.processEvents()
        dialog = window._settings_dialog
        print("dialog:", type(dialog).__name__ if dialog else None)
        # 等入场动画落地
        deadline = 0
        while dialog is not None and deadline < 200:
            app.processEvents()
            deadline += 1
        spin = dialog.spin_vehicle_modules
        print("spin 值:", spin.value(), "尺寸:", spin.width(), "x", spin.height())
        print("内层 edit 尺寸 y:", spin._edit.height(), spin._edit.y())
        measure(spin, tag="[控件grab]")

        # 屏幕抓（用户截图就是这种）：和控件自抓对比，能区分"真偏低"与"抓图假象"
        from PySide6.QtCore import QPoint as _QP
        screen = dialog.screen()
        if screen is not None:
            top_left = spin.mapToGlobal(_QP(0, 0))
            dpr = screen.devicePixelRatio()
            pix = screen.grabWindow(0, top_left.x(), top_left.y(),
                                    spin.width(), spin.height())
            img2 = pix.toImage()
            print("screen grab:", img2.width(), "x", img2.height(), "dpr", dpr)
            bg2 = img2.pixelColor(img2.width() // 2, 6)
            rows2 = ink_rows(img2, bg2, 30, int(img2.width() * 0.6))
            borders2 = [y for y, n in enumerate(rows2)
                        if n > (int(img2.width() * 0.6) - 30) * 0.6]
            body2 = [y for y, n in enumerate(rows2)
                     if 0 < n <= (int(img2.width() * 0.6) - 30) * 0.6
                     and y not in borders2]
            if borders2 and body2:
                print(f"[屏幕抓] 字墨迹 {body2[0]}..{body2[-1]}  "
                      f"上留白 {body2[0] - borders2[0]}  "
                      f"下留白 {borders2[-1] - body2[-1]}  (物理px)")
        for wait_ms in (200, 400, 800, 1500):
            QTimer.singleShot(wait_ms, lambda: None)
            loop_end = wait_ms
            # 用真实等待 + 事件泵，模拟用户"看着它"的那几秒
            import time as _t
            t0 = _t.perf_counter()
            while (_t.perf_counter() - t0) * 1000 < wait_ms:
                app.processEvents()
                _t.sleep(0.005)
            measure(spin, tag=f"[t{loop_end}]")
    finally:
        # 先关窗（closeEvent 会写 geometry/最大化），再还原配置 —— 顺序反了会被它覆盖
        window.close()
        if saved_geo is None:
            settings.remove("geometry")
        else:
            settings.setValue("geometry", saved_geo)
        if saved_max is None:
            settings.remove("custom/startup_maximized")
        else:
            settings.setValue("custom/startup_maximized", saved_max)
        settings.sync()


if __name__ == "__main__":
    main()
