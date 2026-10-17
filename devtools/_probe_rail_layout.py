# -*- coding: utf-8 -*-
"""临时探针（offscreen）：量接收区功能列在普通态/展开态的布局细节。

本会话里源码 show() 真窗会访问违故（exit 127），但布局计算与平台无关 ——
offscreen 下照样能量 stretch、sizePolicy、几何。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QPoint, QSettings          # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402


def pump(app, seconds: float) -> None:
    import time
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.008)


def dump_rail(window, tag):
    rail = window._rx_rail
    lay = rail.layout()
    print(f"--- [{tag}] ---")
    print(f"    rail 几何 {rail.geometry()}  sizePolicy="
          f"{rail.sizePolicy().horizontalPolicy()}/{rail.sizePolicy().verticalPolicy()}")
    items = []
    for i in range(lay.count()):
        it = lay.itemAt(i)
        w = it.widget()
        if w is None:
            items.append(f"stretch(stretch={lay.stretch(i)})")
        else:
            g = w.mapToGlobal(QPoint(0, 0))
            name = getattr(w, "_glyph", None) or (
                "串口开关行" if w is window._open_row else w.__class__.__name__)
            items.append(f"{name}@y{g.y()},{w.width()}x{w.height()}")
    print("    " + " | ".join(items))
    # 父布局里 rail 占的格子
    holder = rail.parentWidget()
    if holder is not None and holder.layout() is not None:
        idx = holder.layout().indexOf(rail)
        print(f"    父布局中 rail 的 index={idx}, "
              f"stretch={holder.layout().stretch(idx)}")


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
    window.resize(1180, 860)
    # offscreen 下不 show 也可能没布局；show 一下（offscreen 的 show 不建真窗）
    window.show()
    pump(app, 0.8)

    try:
        dump_rail(window, "普通态")
        window._expand_receive()
        pump(app, 1.2)          # 等 240ms 的搬家定时器
        dump_rail(window, "展开态（搬家后）")
        window._collapse_receive()
        pump(app, 1.2)
        dump_rail(window, "收起后")
        # 再来一轮：第二次展开（排查 stretch 索引漂移）
        window._expand_receive()
        pump(app, 1.2)
        dump_rail(window, "第二次展开")
        window._collapse_receive()
        pump(app, 1.2)
        dump_rail(window, "第二次收起")
    finally:
        for k, v in snapshot.items():
            settings.setValue(k, v)
        settings.sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
