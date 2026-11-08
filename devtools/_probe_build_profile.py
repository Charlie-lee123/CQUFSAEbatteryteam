# -*- coding: utf-8 -*-
r"""MainWindow 构造耗时剖析（cProfile）—— 目的：砍掉开屏期间那 ~1s 的静止。

用户要求「窗口出现 → 黑屏不动」再快些。开屏是静态海报在等主窗构造，
构造越快，静止越短（或越容易塞进视频自带的静止段）。
QSettings 全量快照/还原。QT_QPA_PLATFORM=windows。
"""
import cProfile
import os
import pstats
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_SNAPSHOT: dict = {}


def main() -> int:
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    app = QApplication([])
    app.setStyle("Fusion")
    import ui_theme
    import customization
    from ui_theme import apply_scheme, ui_font
    mode = str(QSettings("MimoWorks", "SerialAssistant").value(
        "theme_mode", "system") or "system")
    scheme = ui_theme.detect_system_scheme() if mode == "system" else mode
    apply_scheme(scheme)
    app.setFont(ui_font(12.5))
    import ui_anim
    ui_anim.set_frame_hz(ui_anim.display_max_hz())

    from main_window import MainWindow
    import time
    t0 = time.perf_counter()
    pr = cProfile.Profile()
    pr.enable()
    window = MainWindow()
    pr.disable()
    dt = (time.perf_counter() - t0) * 1000
    print(f"\nMainWindow() 总耗时 = {dt:.0f} ms\n")
    stats = pstats.Stats(pr)
    stats.sort_stats("tottime").print_stats(22)

    window.close()
    st2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st2.allKeys()) - set(_SNAPSHOT):
        st2.remove(k)
    for k, v in _SNAPSHOT.items():
        st2.setValue(k, v)
    st2.sync()
    print("PROBE_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
