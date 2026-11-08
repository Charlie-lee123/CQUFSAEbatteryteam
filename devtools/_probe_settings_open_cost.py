# -*- coding: utf-8 -*-
r"""探针：点「设置」到底卡多久 —— 把 ``_open_settings`` 的每一步拆开计时。

背景：逐帧探针（_probe_panel_frames.py）量到 ``window._open_settings()`` 这一个
**同步调用**就占住主线程 0.7~1.5 秒，而面板放大动画只有 260ms。也就是说用户点下
设置按钮之后，先有一段"什么都不动"的卡顿，动画才开始。

这里把 ``_open_settings`` 里的三步（离屏 show+等暴露 / PrintWindow 抓整窗 /
快照上屏）各自套一层计时，剩下的差值就是 ``SettingsDialog(...)`` 构造本体的成本。
连开连关 3 轮，看方差。

用法：.venv\Scripts\python.exe devtools/_probe_settings_open_cost.py
产物：_check/settings_open_cost.txt
"""
from __future__ import annotations

import os
import statistics
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings                                  # noqa: E402
from PySide6.QtWidgets import QApplication                            # noqa: E402

OUT = os.path.join(_ROOT, "_check", "settings_open_cost.txt")
_lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    _lines.append(text)


def pump(app, ms: float) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, ui_font
    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    import customization
    import ui_anim
    hz = int(customization.get("anim_fps"))
    if hz == 120:
        hz = ui_anim.display_max_hz()
    ui_anim.set_frame_hz(hz)

    from selftest import make_window, restore_settings
    settings = QSettings("MimoWorks", "SerialAssistant")
    settings.sync()
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    window = None
    try:
        window = make_window()
        window.setGeometry(60, 40, 1134, 820)
        window.show()
        pump(app, 800)
        say(f"=== 环境 ===  帧率 {hz}Hz  窗口 {window.width()}x{window.height()}")

        marks: dict[str, float] = {}
        # 给三个可替换的步骤套计时（方法级包装，不改产品代码）
        original_expose = window._pump_until_exposed
        original_grab = window._grab_settings_frame
        original_show = window._show_settings_shot

        def timed_expose(dialog, timeout_ms: int = 200):
            begin = time.perf_counter()
            result = original_expose(dialog, timeout_ms)
            marks["expose"] = (time.perf_counter() - begin) * 1000.0
            return result

        def timed_grab():
            begin = time.perf_counter()
            result = original_grab()
            marks["grab"] = (time.perf_counter() - begin) * 1000.0
            marks["pix"] = (result.width(), result.height()) if result is not None else None
            return result

        def timed_show(shot):
            begin = time.perf_counter()
            result = original_show(shot)
            marks["show"] = (time.perf_counter() - begin) * 1000.0
            return result

        window._pump_until_exposed = timed_expose
        window._grab_settings_frame = timed_grab
        window._show_settings_shot = timed_show

        say(f"\n  {'轮':>3}{'总耗时':>10}{'构造(差)':>11}{'等暴露':>9}"
            f"{'PrintWindow':>13}{'快照上屏':>10}{'关':>8}")
        totals = []
        for index in range(3):
            marks.clear()
            begin = time.perf_counter()
            window._open_settings()
            total = (time.perf_counter() - begin) * 1000.0
            totals.append(total)
            dialog = window._settings_dialog
            own = total - marks.get("expose", 0) - marks.get("grab", 0) - marks.get("show", 0)
            say(f"  {index + 1:>3}{total:>9.0f}ms{own:>10.0f}ms"
                f"{marks.get('expose', 0):>8.0f}ms{marks.get('grab', 0):>12.0f}ms"
                f"{marks.get('show', 0):>9.0f}ms")
            if dialog is not None:
                say(f"       位图 = {marks.get('pix')}  "
                    f"面板 = {dialog.frameGeometry().width()}x"
                    f"{dialog.frameGeometry().height()}")
            pump(app, 500)
            begin = time.perf_counter()
            window._close_settings_dialog(accepted=False)
            close_ms = (time.perf_counter() - begin) * 1000.0
            say(f"       关闭同步耗时 {close_ms:.0f}ms")
            pump(app, 900)
        say(f"\n  打开合计：{['%.0f' % v for v in totals]}ms  "
            f"中位 {statistics.median(totals):.0f}ms")
        say("  对照：面板放大动画本身 260ms；165Hz 一帧预算 6.06ms")
        return 0
    finally:
        if window is not None:
            try:
                if window._settings_dialog is not None:
                    window._close_settings_dialog(accepted=False)
                window.close()
            except RuntimeError:
                pass
        pump(app, 300)
        restore_settings(settings, snapshot)
        settings.sync()
        with open(OUT, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_lines))
        print(f"产物：{OUT}")


if __name__ == "__main__":
    sys.exit(main())
