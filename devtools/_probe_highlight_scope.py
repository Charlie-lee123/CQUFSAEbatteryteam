# -*- coding: utf-8 -*-
"""探针：异常高亮的染色范围（越界条目含序号？未越界的不被波及？开关生效？）。

只读，可在主工作区安全运行（自带 QSettings 快照还原）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


def main() -> None:
    import customization
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
        # 上限 3825 mV：三个点里只有 3827 越界 —— 正好测"别波及其它正常点"
        customization.set("volt_limit_high", 3825)
        customization.set("highlight_errors", True)
        window._rx_highlighter.refresh()
        window._clear_receive()
        window._on_received("Voltage1:\n 1.3820   2.3824   3.3827\n".encode("utf-8"))
        window._flush_rx()
        app.processEvents()

        def painted(number: int) -> list:
            block = window.txt_rx.document().findBlockByNumber(number)
            layout = block.layout()
            return [(r.start, r.length, r.format.foreground().color().name())
                    for r in layout.formats()]

        hmm = window._rx_highlighter._high_fmt.foreground().color().name()
        idx = window._rx_highlighter._index_fmt.foreground().color().name()
        print(f"红={hmm}  序号蓝={idx}")
        for b in range(window.txt_rx.document().blockCount()):
            print(f"{b}: {window.txt_rx.document().findBlockByNumber(b).text()!r}")
            print(f"    spans={painted(b)}")

        data_text = window.txt_rx.document().findBlockByNumber(2).text()
        print(f"数据行={data_text!r} 长度={len(data_text)}")
        spans = painted(2)
        red = [(s, n) for s, n, c in spans if c == hmm]
        print(f"红色 span = {red}  （期望只有最后一条 '03.3827' 整条）")
        print(f"期望: 起点 20、长度 7 -> {'符合' if red == [(20, 7)] else '不符合'}")

        # 关掉开关：不该再有任何红/绿色
        customization.set("highlight_errors", False)
        window._rx_highlighter.refresh()
        app.processEvents()
        spans_off = painted(2)
        red_off = [s for s, n, c in spans_off if c == hmm]
        blues = sorted(s for s, n, c in spans_off if c == idx)
        print(f"\n开关关闭后：红 span={red_off}（应为空）  序号蓝起点={blues}")
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
