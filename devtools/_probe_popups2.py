# -*- coding: utf-8 -*-
"""探针 2：诊断为什么非编辑下拉的弹层没走新样式。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QTimer
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

    combos = [
        ("baud", win.cmb_baud),
        ("stopbits", win.cmb_stopbits),
        ("encoding", win.cmb_encoding),
    ]

    def dump(tag: str, combo) -> None:
        c = combo.view().window()
        print(f"--- {tag} ---")
        print("  cls:", type(combo).__name__, "editable:", combo.isEditable(),
              "count:", combo.count())
        print("  combo styleSheet:", repr(combo.styleSheet()[:80]))
        print("  container flags translucent:",
              bool(c.windowFlags() & Qt.WindowType.Popup),
              c.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground),
              "wflag:", hex(int(c.windowFlags())))
        print("  view minw:", combo.view().minimumWidth(),
              "container geo:", c.geometry())
        print("  view styleSheet:", repr(combo.view().styleSheet()[:80]))

    idx = {"i": 0}

    def step() -> None:
        if idx["i"] >= len(combos):
            app.quit()
            return
        tag, combo = combos[idx["i"]]
        combo.showPopup()
        QTimer.singleShot(250, lambda: (dump(tag, combo),
                                        combo.hidePopup(),
                                        _next()))

    def _next() -> None:
        idx["i"] += 1
        QTimer.singleShot(120, step)

    QTimer.singleShot(400, step)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
