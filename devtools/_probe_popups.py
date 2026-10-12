# -*- coding: utf-8 -*-
"""探针：逐个打开各下拉的弹层并截图，对比是否与波特率观感一致。

起手与 main.py 一致：Fusion + ui_font(12.5) + apply_scheme。
输出 _check/popup_<名字>_*.png（展开中 / 完全展开 两帧）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QComboBox

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

    from ui_settings import SettingsDialog
    dlg = SettingsDialog(win)
    dlg.show()
    app.processEvents()

    out = Path(__file__).resolve().parent.parent / "_check"
    combos = [
        ("baud", win.cmb_baud),
        ("stopbits", win.cmb_stopbits),
        ("bytesize", win.cmb_bytesize),
        ("parity", win.cmb_parity),
        ("encoding", win.cmb_encoding),
        ("rx_lines", win.cmb_rx_lines),
        ("history", win.cmb_history),
        ("theme", dlg.cmb_theme),
    ]

    results = []
    idx = {"i": 0}

    def shot(tag: str, combo: QComboBox, label: str) -> None:
        container = combo.view().window()
        img = container.grab()
        p = out / f"popup_{tag}_{label}.png"
        img.save(str(p))
        results.append(f"{tag}/{label}: geo={container.geometry().width()}x"
                       f"{container.geometry().height()} "
                       f"minw={combo.view().minimumWidth()} saved={p.name}")

    def next_combo() -> None:
        if idx["i"] >= len(combos):
            combo = None
        else:
            combo = combos[idx["i"]][1]
        if combo is not None:
            tag = combos[idx["i"]][0]
            combo.showPopup()
            # 展开中（约一半动画处）与完全展开各来一张
            QTimer.singleShot(40, lambda: shot(tag, combo, "mid"))
            QTimer.singleShot(250, lambda: shot(tag, combo, "full"))
            QTimer.singleShot(300, lambda: (combo.hidePopup(), advance()))
        else:
            print("\n".join(results))
            app.quit()

    def advance() -> None:
        idx["i"] += 1
        QTimer.singleShot(120, next_combo)

    QTimer.singleShot(400, next_combo)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
