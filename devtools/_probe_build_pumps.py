# -*- coding: utf-8 -*-
r"""量「两次让出事件循环之间」的间隔，找出 267ms 那段落在哪个构造段。

背景
----
`_probe_splash_gap.py` 报的最大堵点是 **~267ms**，栈顶 `_build_config_panel`
建下拉框；但 `_probe_combo_ctor.py` 证明 `ChevronComboBox()` 只要 0.2ms，
真流程 cProfile 里 `MainWindow()` 总共才 358ms、最大单项也只有 `winId` 68ms。
⇒ 267ms 是**一次没插到 `_pump_build` 的连续段**，不是单个原子 C++ 调用。

`main_window._pump_build` 是每 30ms 让出一次主线程的模块级函数（时间片）。
本探针把它包一层，记录每次让出的**时刻 + 调用处栈**，然后按间隔降序打印：
间隔对应「上一段构造跑了多久」，两端栈就是这段构造的起点/终点。

⛔ 只读探针：不改产品源码、不打包、不写 QSettings。
"""
from __future__ import annotations

import os
import sys
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RUN_MS = 7000
_pumps: list[tuple[float, list[str]]] = []


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    import main_window

    _orig = main_window._pump_build

    def _wrapped() -> None:
        st = traceback.format_stack()[-3:-1]
        _pumps.append((time.perf_counter(), st))
        _orig()

    main_window._pump_build = _wrapped

    import main as app_main

    _orig_exec = QApplication.exec
    QApplication.exec = lambda *a: (QTimer.singleShot(
        RUN_MS, QApplication.instance().quit), _orig_exec())

    try:
        app_main.main()
    except SystemExit:
        pass
    except Exception:                      # noqa: BLE001
        traceback.print_exc()

    print(f"\n=== 构造期让出事件循环 {len(_pumps)} 次；最大间隔排名 ===")
    ivs = []
    for i in range(1, len(_pumps)):
        ivs.append(((_pumps[i][0] - _pumps[i - 1][0]) * 1000.0, i))
    for dur, i in sorted(ivs, key=lambda x: -x[0])[:6]:
        print(f"\n--- 间隔 {dur:.0f} ms ---")
        print("  起点（上一次让出的调用处）：")
        for line in _pumps[i - 1][1]:
            print("    " + line.rstrip())
        print("  终点（这次让出的调用处）：")
        for line in _pumps[i][1]:
            print("    " + line.rstrip())
    if _pumps:
        span = (_pumps[-1][0] - _pumps[0][0]) * 1000.0
        print(f"\n让出总跨度 {span:.0f} ms，平均 "
              f"{span / max(1, len(_pumps) - 1):.1f} ms/次")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
