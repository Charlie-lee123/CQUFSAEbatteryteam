# -*- coding: utf-8 -*-
"""探针：性能模式浮层的实际观感（真窗口 + 屏幕截图）。

为什么用 ``PIL.ImageGrab`` 而不是 ``QScreen.grabWindow``：项目里量过多次 ——
后者拿不到 DWM 合成后的画面，半透明浮层会截成空白/底色。

用法::

    .venv/Scripts/python.exe devtools/_probe_perf_overlay.py

产出 ``_check/perf_overlay/*.png``（进入：0.25s / 0.6s / 1.6s；退出：0.3s）。
用完自动关窗，不写任何用户配置。
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEventLoop, QTimer                          # noqa: E402
from PySide6.QtWidgets import QApplication                             # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "_check", "perf_overlay")


def _spin(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _shot(window, name: str) -> None:
    from PIL import ImageGrab

    geo = window.frameGeometry()
    dpr = window.devicePixelRatioF()
    box = (int(geo.x() * dpr), int(geo.y() * dpr),
           int((geo.x() + geo.width()) * dpr),
           int((geo.y() + geo.height()) * dpr))
    ImageGrab.grab(bbox=box, all_screens=True).save(
        os.path.join(OUT, name), "PNG")
    print("saved", name, box)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    QApplication(sys.argv)
    import main_window

    window = main_window.MainWindow()
    window.show()
    _spin(700)                       # 等入场动画/布局落定

    window._emit_block("Voltage1:\n01.4211   02.4211   03.3051 X   04.5209\n"
                       "BMS Error\nerrorTime=0\n1.19 Under Voltage\n"
                       "COM0=0\nV=0.00,I=-250.00\nCVMax=5433, Min=0")
    _spin(300)

    t0 = time.perf_counter()
    window._set_perf_mode(True)
    print(f"进入手势 → 浮层起跑 {1000 * (time.perf_counter() - t0):.0f}ms")
    _spin(250)
    _shot(window, "enter_0250.png")
    _spin(350)
    _shot(window, "enter_0600.png")
    _spin(1000)
    _shot(window, "enter_1600.png")

    window._set_perf_mode(False)
    _spin(300)
    _shot(window, "exit_0300.png")
    _spin(1600)

    window.close()
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
