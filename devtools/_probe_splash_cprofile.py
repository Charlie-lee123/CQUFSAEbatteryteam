# -*- coding: utf-8 -*-
r"""在**真实启动流程**里对 `MainWindow()` 做 cProfile：把开屏期间那 ~267ms 归因。

为什么另起一个探针
------------------
`_probe_build_profile.py` 是**裸进程**单独构造 MainWindow（自己 new QApplication、
没有开屏、字体引擎冷着）——它测出来的 top 是 `QFontMetrics.height()` 544ms，
和真实启动里的现象对不上：真机开屏首帧已经把字体引擎付掉了
（`_warm_font_on_main` 实测只花 7.1ms 就是证据），而且 `_probe_combo_ctor.py`
证明 `QComboBox` 构造只要 0.2ms。所以必须**在真流程里**量。

做法
----
和 `_probe_splash_gap.py` 同一套启动方式（直接跑 `main.main()`），只把
`main_window.MainWindow.__init__` 包一层 cProfile。构造结束后按 `tottime`
打印前 25 名（tottime 已扣子调用，C++ 调用记在发起它的 Python 函数名下）。

⛔ 只读探针：不改产品源码、不打包、不写 QSettings。
"""
from __future__ import annotations

import cProfile
import io
import os
import pstats
import sys
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RUN_MS = 7000


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    import main_window

    _orig = main_window.MainWindow.__init__

    def _wrapped(self, *a, **kw):
        t0 = time.perf_counter()
        pr = cProfile.Profile()
        pr.enable()
        try:
            _orig(self, *a, **kw)
        finally:
            pr.disable()
            dt = (time.perf_counter() - t0) * 1000.0
            print(f"\n=== 真流程 MainWindow() 构造 = {dt:.0f} ms ===", flush=True)
            buf = io.StringIO()
            pstats.Stats(pr, stream=buf).sort_stats("tottime").print_stats(25)
            for line in buf.getvalue().splitlines():
                if "tottime" in line or "ncalls" in line:
                    print(line, flush=True)
                    continue
                print(line, flush=True)
            print("=== 构造剖析结束 ===", flush=True)

    main_window.MainWindow.__init__ = _wrapped

    import splash_anim
    _ow = getattr(splash_anim, "_warm_font_on_main", None)
    if _ow is not None:
        def _w():
            t = time.perf_counter()
            try:
                return _ow()
            finally:
                print(f"[warm] _warm_font_on_main = "
                      f"{(time.perf_counter() - t) * 1000:.1f}ms", flush=True)
        splash_anim._warm_font_on_main = _w

    import main as app_main

    _orig_exec = QApplication.exec
    QApplication.exec = lambda *a: (QTimer.singleShot(RUN_MS,
                                                     QApplication.instance().quit),
                                    _orig_exec())[1]

    try:
        app_main.main()
    except SystemExit:
        pass
    except Exception:                      # noqa: BLE001
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
