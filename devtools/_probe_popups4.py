# -*- coding: utf-8 -*-
"""探针 4：解剖弹层容器的子控件结构。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget

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

    combos = [("baud", win.cmb_baud), ("stopbits", win.cmb_stopbits)]
    idx = {"i": 0}

    def step() -> None:
        if idx["i"] >= len(combos):
            app.quit()
            return
        tag, combo = combos[idx["i"]]
        combo.showPopup()

        def dump() -> None:
            c = combo.view().window()
            print(f"=== {tag} container {c.geometry().width()}x"
                  f"{c.geometry().height()} cls={type(c).__name__}")
            for ch in c.children():
                if isinstance(ch, QWidget):
                    print(f"   child {type(ch).__name__} geo={ch.geometry()}")
            v = combo.view()
            print(f"   view geo={v.geometry()} viewport={v.viewport().geometry()}"
                  f" vhint={v.sizeHint()} chint={c.sizeHint()} minH={v.minimumHeight()}")
            lay = c.layout()
            if lay is not None:
                print(f"   layout {type(lay).__name__} contentsMargins="
                      f"{lay.contentsMargins()} spacing={lay.spacing()}")
                for i in range(lay.count()):
                    it = lay.itemAt(i)
                    print(f"     item {type(it.widget()).__name__ if it.widget() else it}"
                          f" geo={it.geometry()}")
            combo.hidePopup()
            idx["i"] += 1
            QTimer.singleShot(150, step)

        QTimer.singleShot(280, dump)

    QTimer.singleShot(400, step)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
