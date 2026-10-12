# -*- coding: utf-8 -*-
"""探针 3：屏幕级截图对比弹层真实渲染，并量容器/view 的几何。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from ui_theme import apply_scheme, ui_font


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))

    from main_window import MainWindow
    apply_scheme(app)
    win = MainWindow()
    win.show()
    app.processEvents()

    screen = QGuiApplication.primaryScreen()
    out = Path(__file__).resolve().parent.parent / "_check"

    combos = [("baud", win.cmb_baud), ("stopbits", win.cmb_stopbits),
              ("encoding", win.cmb_encoding)]
    idx = {"i": 0}

    def step() -> None:
        if idx["i"] >= len(combos):
            app.quit()
            return
        tag, combo = combos[idx["i"]]
        combo.showPopup()
        def snap_full() -> None:
            c = combo.view().window()
            v = combo.view()
            print(f"--- {tag}: container={c.geometry().width()}x"
                  f"{c.geometry().height()} view geo={v.geometry().width()}x"
                  f"{v.geometry().height()} view sizehint={v.sizeHint().width()}x"
                  f"{v.sizeHint().height()} container sizehint="
                  f"{c.sizeHint().width()}x{c.sizeHint().height()}")
            screen.grabWindow(0).save(str(out / f"screen_{tag}.png"))
            combo.hidePopup()
            idx["i"] += 1
            QTimer.singleShot(150, step)
        QTimer.singleShot(280, snap_full)

    QTimer.singleShot(400, step)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
