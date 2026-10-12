# -*- coding: utf-8 -*-
"""探针：复现"复制本轮数据不完整 + 时间戳出现在非开头位置"。

只读，可在主工作区安全运行（自带 QSettings 快照还原）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


def round_body(tag: str, seed: int) -> list[str]:
    """一轮真实形态的数据，按"分包"拆成若干帧返回。"""
    frames = [
        f"Voltage1:\n" + " ".join(f"{j}.38{seed}{j % 10}" for j in range(1, 21)),
        f"Voltage1:\n" + " ".join(f"{j}.38{seed}{j % 10}" for j in range(21, 41)),
        "Temperature1:\n" + " ".join(f"{j}.2{seed}" for j in range(1, 41)),
        f"BMS OK",
    ]
    return frames


def main() -> None:
    from main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    window = MainWindow()
    try:
        window.resize(1000, 800)
        window.show()
        app.processEvents()
        window.chk_timestamp.setChecked(True)
        window._clear_receive()

        # 喂 5 轮，每轮 4 帧（第 1、2 帧都含 Voltage1 —— 真实设备分块发电压）
        for r in range(5):
            for frame in round_body("r", r + 1):
                window._emit_block(frame)
                app.processEvents()
            window._emit_block("")          # 轮间空帧
            app.processEvents()

        doc = window.txt_rx.document()
        stamps = [b for b in range(doc.blockCount())
                  if doc.findBlockByNumber(b).text().startswith("[")]
        print(f"块数={doc.blockCount()}  时间戳行号={stamps}")
        print("--- 文档全文 ---")
        for b in range(doc.blockCount()):
            print(f"{b:3d}| {doc.findBlockByNumber(b).text()}")
        print("--- 复制（视口底部，最后一轮）---")
        bar = window.txt_rx.verticalScrollBar()
        bar.setValue(bar.maximum())
        app.processEvents()
        window._copy_all_receive()
        got = QApplication.clipboard().text()
        print(repr(got))
        lines = got.split("\n")
        print(f"行数={len(lines)}  首行={lines[0]!r}")
        inner_stamps = [i for i, ln in enumerate(lines)
                        if ln.startswith("[") and i > 0]
        print(f"开头以外的时间戳行索引={inner_stamps}")
    finally:
        window.close()
        app.processEvents()
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()


if __name__ == "__main__":
    main()
