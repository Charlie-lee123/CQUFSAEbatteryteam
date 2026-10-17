# -*- coding: utf-8 -*-
r"""抓下拉弹层的真窗口图 + 量条目几何（排查"哪个下拉不对劲"用）。

一次抓三个：校验位（不可编辑）/ 编码（可编辑）/ 波特率（可编辑、条目最多）。
量的是：条目行高、行距、viewport 宽度、条目矩形宽度、delegate 类型。

⚠️ 关窗**前**必须把窗口搬回屏幕内：main_window 的 closeEvent 会把当前几何写进
   注册表，屏幕外坐标一旦写进去，之后全屏往返的位置会被系统夹回 (0,0)。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings                        # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

OUT = os.path.join(_ROOT, "_check")


def shoot_one(app, cmb, name: str) -> None:
    try:
        editable = cmb.isEditable()
    except Exception:
        editable = "?"
    print(f"\n== {name}: editable={editable} 条目数={cmb.count()} "
          f"delegate={type(cmb.view().itemDelegate()).__name__}")
    cmb.showPopup()
    end = time.time() + 0.6
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

    view = cmb.view()
    container = view.window()
    r0 = view.visualRect(view.model().index(0, 0))
    r1 = view.visualRect(view.model().index(1, 0))
    print(f"   容器 {container.width()}x{container.height()}  "
          f"行高={r0.height()} 行距={r1.y() - r0.y()}  "
          f"viewport={view.viewport().width()} 条目宽={r0.width()}")
    print(f"   view 字体={view.font().family()} {view.font().pointSizeF()}pt  "
          f"combo 字体={cmb.font().family()} {cmb.font().pointSizeF()}pt")

    path = os.path.join(OUT, f"popup_{name}.png")
    print("   抓图:", container.grab().save(path), path)
    cmb.hidePopup()
    for _ in range(20):
        app.processEvents()


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
    try:
        apply_scheme("dark")
        window = MainWindow()
        window._port_watch.stop()
        window.setGeometry(-4000, -4000, 1180, 940)
        window.show()
        for _ in range(60):
            app.processEvents()

        os.makedirs(OUT, exist_ok=True)
        for name, cmb in (("parity", window.cmb_parity),
                          ("encoding", window.cmb_encoding),
                          ("baud", window.cmb_baud)):
            shoot_one(app, cmb, name)

        # ⚠️ 关窗前搬回屏幕内（见模块头注释）
        window.setGeometry(0, 0, 1134, 820)
        window.close()
    finally:
        for key, value in snapshot.items():
            if value is not None:
                settings.setValue(key, value)
        settings.sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
