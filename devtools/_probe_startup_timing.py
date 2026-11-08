# -*- coding: utf-8 -*-
r"""测量启动各阶段耗时（复刻 main() 的调用顺序，2026-11-03）。

目的：用户报"开屏动画之前停住很久"。录屏实测双击→窗口出现 3.4s、纯黑静止 1.1s。
本探针把 3.4s 拆成：import 全家桶 / QApplication / apply_scheme / ui_font /
ui_anim / 开屏创建 / MainWindow 构造 / reveal，定位最大头。
QSettings 全量快照/还原。QT_QPA_PLATFORM=windows。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings, QTimer  # noqa: E402

_SNAPSHOT: dict = {}
T = {}
_t = [time.perf_counter()]


def mark(key: str) -> None:
    now = time.perf_counter()
    T[key] = (now - _t[0]) * 1000.0
    _t[0] = now


def main() -> int:
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    # --- 复刻 main.py 顶部 import（全家桶） --------------------------------
    import customization                                   # noqa: F401
    from main_window import MainWindow                      # noqa: F401
    from ui_brand import app_icon
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    mark("1) import 全家桶（main_window/ui_theme/…）")

    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    app.setApplicationName("CQUFSAE串口助手")
    app.setOrganizationName("MimoWorks")
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")
    mark("2) QApplication + 图标 + Fusion")

    _theme_mode = str(QSettings("MimoWorks", "SerialAssistant").value(
        "theme_mode", "system") or "system")
    _scheme = detect_system_scheme() if _theme_mode == "system" else _theme_mode
    apply_scheme(_scheme)
    mark("3) apply_scheme（生成 QSS）")

    app.setFont(ui_font(12.5))
    mark("4) ui_font 字体加载")

    import ui_anim
    _fps = int(customization.get("anim_fps"))
    if _fps == 120:
        _fps = ui_anim.display_max_hz()
    ui_anim.set_frame_hz(_fps)
    mark("5) ui_anim 帧率设置")

    import splash_anim
    sp = splash_anim.play_configured()
    mark("6) 开屏创建（play_configured）")

    window = MainWindow()
    mark("7) MainWindow 构造")

    window._port_watch.stop() if hasattr(window, "_port_watch") else None
    window._skip_startup_anim = True
    window.show()
    window.apply_startup_state()
    mark("8) show + apply_startup_state（主窗就就位）")

    print("\n=== 启动阶段耗时 ===")
    total = 0.0
    for k, v in T.items():
        total += v
        print(f"  {v:8.1f} ms  {k}")
    print(f"  {total:8.1f} ms  === 合计（不含 exe 解包/Python 启动）===")

    QTimer.singleShot(400, app.quit)

    def cleanup():
        pass

    app.exec()
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
