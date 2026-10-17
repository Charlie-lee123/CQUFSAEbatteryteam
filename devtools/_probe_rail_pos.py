# -*- coding: utf-8 -*-
"""临时探针：量接收区功能列（rail）在普通态/展开态的真实位置 + 截图。

目的：用户截图说"把这个放回去 / 放左边一点"，先把 rail 相对数据卡片的
位置量出来（普通态、展开态各一次），确认到底是偏右、偏下还是别的。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QPoint, QSettings          # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402


def pump(app, seconds: float) -> None:
    import time
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.008)


def measure(window, tag: str) -> None:
    rail = window._rx_rail
    card = window._rx_card if hasattr(window, "_rx_card") else None
    rail_rect = rail.geometry()
    rail_global = rail.mapToGlobal(QPoint(0, 0))
    print(f"[{tag}] rail 全局 x={rail_global.x()} y={rail_global.y()} "
          f"w={rail_rect.width()} h={rail_rect.height()}")
    # 列里每颗键的全局位置
    lay = rail.layout()
    names = []
    for i in range(lay.count()):
        item = lay.itemAt(i)
        w = item.widget()
        if w is None:
            names.append(f"<stretch{item.stretch()}>")
            continue
        g = w.mapToGlobal(QPoint(0, 0))
        label = getattr(w, "_glyph", None) or w.__class__.__name__
        names.append(f"{label}@({g.x()},{g.y()},{w.width()}x{w.height()})")
    print(f"   列内容: {' | '.join(names)}")
    if card is not None:
        cg = card.mapToGlobal(QPoint(0, 0))
        cgeo = card.geometry()
        print(f"   卡片 全局 x={cg.x()} y={cg.y()} w={cgeo.width()} h={cgeo.height()}")
        print(f"   >>> 卡片右缘 -> 列左缘 水平间距 = {rail_global.x() - (cg.x() + cgeo.width())}")
        print(f"   >>> 卡片顶 -> 列里第一颗键 竖直间距 = "
              f"{names and '见上' or ''}")


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
    print("window built", flush=True)
    window._port_watch.stop()
    screen = app.primaryScreen().availableGeometry()
    window.setGeometry(screen.x() + 60, screen.y() + 40, 1180, 860)
    window.show()
    print("shown", flush=True)
    window.raise_()
    window.activateWindow()
    pump(app, 1.2)

    out = os.path.join(_ROOT, "_check")
    os.makedirs(out, exist_ok=True)
    try:
        # ① 普通态（窗口全屏起手，所有卡都在）
        measure(window, "普通态")
        window.grab().save(os.path.join(out, "rail_1_normal.png"))
        print("   截图 rail_1_normal.png")

        # ② 展开态
        window._expand_receive()
        pump(app, 1.6)
        measure(window, "展开态(动画后)")
        window.grab().save(os.path.join(out, "rail_2_expanded.png"))
        print("   截图 rail_2_expanded.png")

        # ③ 收起回普通态
        window._collapse_receive()
        pump(app, 1.6)
        measure(window, "收起后")
        window.grab().save(os.path.join(out, "rail_3_collapsed.png"))
        print("   截图 rail_3_collapsed.png")
    finally:
        # 关窗前搬回屏幕内（closeEvent 会把几何写进注册表）
        window.setGeometry(0, 0, 1134, 820)
        window.close()
        for k, v in snapshot.items():
            settings.setValue(k, v)
        settings.sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
