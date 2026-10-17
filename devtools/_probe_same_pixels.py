# -*- coding: utf-8 -*-
r"""同一个控件、同一块像素：最大化前后逐像素对比。

回答用户的问题："请将界面亮度恢复到与最大化之前相同的状态" —— 到底
**哪些元素**在最大化之后真的变暗了？不做整窗统计（那会被不同布局干扰），
而是把"接收区日志控件"和"右侧功能列每个按钮"按各自窗口坐标分别抓取对比。

用法：python devtools\_probe_same_pixels.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QPoint, QSettings                # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton     # noqa: E402

OUT = os.path.join(_ROOT, "_check")


def stat(img, x, y, w, h):
    """抓窗口内一块区域，返回 (最亮, 均亮, >120 像素数)。"""
    box = img.copy(x, y, w, h)
    total = 0
    n = 0
    brightest = 0
    lit = 0
    for yy in range(0, box.height(), 2):
        for xx in range(0, box.width(), 2):
            c = box.pixelColor(xx, yy)
            v = (c.red() + c.green() + c.blue()) // 3
            total += v
            n += 1
            brightest = max(brightest, v)
            if v > 120:
                lit += 1
    return brightest, (total / n if n else 0.0), lit


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    import customization
    from ui_theme import apply_scheme, ui_font
    from main_window import MainWindow

    for spec in customization.specs():
        customization.set(spec["key"], spec["default"])
    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    window = MainWindow()
    window._port_watch.stop()
    screen = app.primaryScreen().availableGeometry()
    window.setGeometry(screen.x() + 60, screen.y() + 50, 1280, 900)
    window.show()
    window.raise_()
    for _ in range(120):
        app.processEvents()
    time.sleep(0.5)
    for _ in range(40):
        app.processEvents()

    def probe(tag: str) -> dict:
        img = window.grab().toImage()
        # 同一个控件：日志视口（含那句占位提示）
        origin = window.txt_rx.viewport().mapTo(window, QPoint(0, 0))
        box = stat(img, origin.x(), origin.y(),
                   window.txt_rx.viewport().width(),
                   min(60, window.txt_rx.viewport().height()))
        # 右侧功能列：逐个按钮抓
        rail = window._rx_rail
        buttons = rail.findChildren(QPushButton)
        per = []
        for b in buttons:
            o = b.mapTo(window, QPoint(0, 0))
            per.append((b.objectName() or type(b).__name__,
                        stat(img, o.x(), o.y(), b.width(), b.height())))
        print(f"  [{tag}] 日志视口顶部 60px：最亮={box[0]} 均亮={box[1]:.1f} 亮像素={box[2]}")
        for name, s in per:
            print(f"       按钮 {name:<16} 最亮={s[0]:>3} 均亮={s[1]:>5.1f} 亮像素={s[2]}")
        return {"log": box, "buttons": per}

    print("== 同一个控件 / 同一批按钮，最大化前后对比")
    before = probe("最大化前")
    window._expand_receive()
    for _ in range(250):
        app.processEvents()
        time.sleep(0.004)
    time.sleep(0.5)
    for _ in range(60):
        app.processEvents()
    after = probe("最大化后")

    print("\n== 差值（正数=最大化后更暗/更少亮像素）")
    print(f"  日志区亮像素：{before['log'][2]} → {after['log'][2]}   "
          f"最亮 {before['log'][0]} → {after['log'][0]}")
    for (n1, s1), (n2, s2) in zip(before["buttons"], after["buttons"]):
        print(f"  {n1:<16} 最亮 {s1[0]:>3} → {s2[0]:>3}   亮像素 {s1[2]:>4} → {s2[2]:>4}")

    window._collapse_receive()
    for _ in range(250):
        app.processEvents()
        time.sleep(0.004)
    window.setGeometry(0, 0, 1134, 820)
    window.close()
    for key, value in snapshot.items():
        if value is not None:
            settings.setValue(key, value)
    settings.sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
