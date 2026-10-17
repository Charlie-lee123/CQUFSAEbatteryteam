# -*- coding: utf-8 -*-
r"""量"接收区最大化前后"同一批 UI 元素的亮度差。

用户反馈"最大化之后文字和图标还是灰色的、太暗淡，**和最大化之前一样亮就好了**"。
这里在**同一个窗口**里，最大化前后各抓一次，逐块量最亮像素与均值，
判断到底是渲染变暗（元素本身变暗）还是"整屏同一片暗色"的观感问题。

用法：python devtools\_probe_dim_expand.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings                  # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402

OUT = os.path.join(_ROOT, "_check")


def block(img, box, dpr):
    """取一块区域，返回 (最亮, 均亮, 亮于阈值像素数)。"""
    x0, y0, x1, y1 = box
    x0, y0 = int(x0 * dpr), int(y0 * dpr)
    x1, y1 = int(x1 * dpr), int(y1 * dpr)
    x1, y1 = min(x1, img.width()), min(y1, img.height())
    total = 0
    n = 0
    brightest = 0
    lit = 0
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            c = img.pixelColor(x, y)
            v = (c.red() + c.green() + c.blue()) // 3
            total += v
            n += 1
            brightest = max(brightest, v)
            if v > 120:
                lit += 1
    return brightest, (total / n if n else 0), lit


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

    def snapshot_img(tag: str):
        img = window.grab().toImage()
        dpr = img.width() / window.width()
        print(f"  {tag}（窗口 {window.width()}x{window.height()}）")
        boxes = {
            "标题栏文字区": (30, 4, 320, 34),
            "底部状态栏": (12, window.height() - 40, 520, window.height() - 8),
            "右侧功能列": (window.width() - 76, window.height() * 0.34,
                          window.width() - 24, window.height() * 0.72),
            "接收区正文区": (60, 90, 640, 240),
        }
        for name, box in boxes.items():
            mx, avg, lit = block(img, box, dpr)
            print(f"     {name}: 最亮={mx} 均亮={avg:.1f} 亮像素={lit}")
        img.save(os.path.join(OUT, f"dim_{tag}.png"))
        return img

    print("== 同一窗口，最大化前后逐块对比")
    snapshot_img("before")
    window._expand_receive()
    for _ in range(250):
        app.processEvents()
        time.sleep(0.004)
    time.sleep(0.5)
    for _ in range(60):
        app.processEvents()
    snapshot_img("after")

    window._collapse_receive()
    for _ in range(250):
        app.processEvents()
        time.sleep(0.004)
    window.setGeometry(0, 0, 1134, 820)          # 关窗前搬回屏幕内（见项目约定）
    window.close()
    for key, value in snapshot.items():
        if value is not None:
            settings.setValue(key, value)
    settings.sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())