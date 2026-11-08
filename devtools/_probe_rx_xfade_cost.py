# -*- coding: utf-8 -*-
r"""归因探针：接收区「旧图淡出」（``_RxShot``）为什么只有 ~65fps。

假设：``_RxShot`` 是 ``txt_rx.viewport()`` 的**子控件**，且没开
``WA_OpaquePaintEvent`` —— 于是每一帧 Qt 都要先重画父控件（整块富文本
QPlainTextEdit 视口）再画覆盖层，贴图本身只要 0.5ms（见 _probe_alphablit.py），
15ms 全花在"底下的文字被重画 25 次"上。

三组对照（同一段 160ms 淡出，各跑 3 次取中位）：
  ① 现状
  ② 给 ``_rx_shot`` 打开 WA_OpaquePaintEvent（**只是归因实验，不是可交付修法**：
    淡化期间覆盖层是半透明的，父控件不重画会露出陈旧像素）
  ③ 纯贴图（覆盖层 pixmap 预先和"新内容快照"混合成不透明图再 1:1 贴）——
     这条才是可落地方向，这里用"关掉父重绘 + 不透明贴图"近似它的上限

另外单独量 ``viewport.repaint()`` 的一次性成本（= 每帧被白重画的那部分）。

用法：.venv\Scripts\python.exe devtools/_probe_rx_xfade_cost.py
产物：_check/rx_xfade_cost.txt
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

from PySide6.QtCore import QSettings, Qt                                # noqa: E402
from PySide6.QtWidgets import QApplication                             # noqa: E402

OUT = os.path.join(_ROOT, "_check", "rx_xfade_cost.txt")
_lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    _lines.append(text)


def pump(app, ms: float) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


def sample_fade(app, window, ticks: list[float], settle_ms: float = 400.0) -> list[float]:
    """跑一段 160ms 旧图淡出，返回补间帧间隔（ms）。"""
    import ui_anim
    ticker = ui_anim._ticker()
    original = ticker._tick
    ticks.clear()

    def patched() -> None:
        original()
        ticks.append(time.perf_counter())

    ticker._tick = patched
    try:
        shot = window._grab_rx_shot()
        window._show_rx_shot(shot)
        window._fade_rx_shot()
        deadline = time.perf_counter() + settle_ms / 1000.0
        while time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.002)
    finally:
        ticker._tick = original
    return [(b - a) * 1000.0 for a, b in zip(ticks, ticks[1:])]


def report(title: str, intervals: list[list[float]], hz: int) -> None:
    nominal = 1000.0 / hz
    flat = [iv for group in intervals for iv in group if iv < 40.0]
    if not flat:
        say(f"  {title}: 没采到帧")
        return
    flat.sort()
    p50 = statistics.median(flat)
    drops = sum(1 for iv in flat if iv > 1.5 * nominal)
    say(f"  {title:<28} 帧数={len(flat):>3}  P50={p50:5.2f}ms "
        f"({1000.0 / p50:6.1f}fps)  P95={flat[int(len(flat) * 0.95) - 1]:5.2f}ms  "
        f"最大={flat[-1]:5.2f}ms  掉帧={drops}/{len(flat)}")


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
    hz = ui_anim.set_frame_hz(hz)

    from selftest import make_window, restore_settings
    settings = QSettings("MimoWorks", "SerialAssistant")
    settings.sync()
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    window = None
    try:
        window = make_window()
        window.setGeometry(60, 40, 1134, 820)
        window.show()
        pump(app, 600)
        payload = ("Voltage1:\n" + " ".join(f"{i:02d}.3700" for i in range(1, 76))
                   + "\nTemperature1:\n" + " ".join(f"{i:02d}.25" for i in range(1, 20)))
        window._on_received(payload.encode("utf-8"))
        window._flush_rx()
        pump(app, 700)
        viewport = window.txt_rx.viewport()
        say(f"=== 环境 ===  帧率 {hz}Hz（名义 {1000.0 / hz:.2f}ms）  "
            f"viewport {viewport.width()}x{viewport.height()} "
            f"@dpr{viewport.devicePixelRatioF():.0f}  "
            f"块数 {window.txt_rx.document().blockCount()}")

        ticks: list[float] = []
        # ① 现状（3 次）
        runs = [sample_fade(app, window, ticks) for _ in range(3)]
        report("① 现状", runs, hz)
        # ② 只开 WA_OpaquePaintEvent（归因实验）
        window._rx_shot.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        runs = [sample_fade(app, window, ticks) for _ in range(3)]
        report("② +WA_OpaquePaintEvent", runs, hz)
        # ③ 再把 alpha 全程钉 1.0（= "预混合成不透明图" 的上限近似）
        original_step = window._rx_xfade_step

        def opaque_step(value: float) -> None:
            original_step(1.0)

        window._rx_xfade_step = opaque_step
        runs = [sample_fade(app, window, ticks) for _ in range(3)]
        report("③ ②+不透明贴图(上限近似)", runs, hz)
        window._rx_xfade_step = original_step
        window._rx_shot.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        # 单独量一次全视口重绘成本
        costs = []
        for _ in range(20):
            begin = time.perf_counter()
            viewport.repaint()
            costs.append((time.perf_counter() - begin) * 1000.0)
        costs.sort()
        say(f"\n  viewport.repaint() 整块重绘：P50={statistics.median(costs):.2f}ms "
            f"P95={costs[int(len(costs) * 0.95) - 1]:.2f}ms max={costs[-1]:.2f}ms")
        say(f"  → 若覆盖层每帧都要父控件重画一次，160ms 里 26 帧 × "
            f"{statistics.median(costs):.1f}ms = "
            f"{26 * statistics.median(costs):.0f}ms 的纯赔")

        # 把覆盖层**停在半透明**，单独量它自己的一次同步重绘
        shot = window._grab_rx_shot()
        window._show_rx_shot(shot)
        window._rx_shot.sync_geometry()
        window._rx_shot.set_alpha(0.5)
        pump(app, 120)
        over = []
        for _ in range(20):
            begin = time.perf_counter()
            window._rx_shot.repaint()
            over.append((time.perf_counter() - begin) * 1000.0)
        over.sort()
        say(f"  _rx_shot.repaint()（覆盖层自己，alpha=0.5）："
            f"P50={statistics.median(over):.2f}ms "
            f"P95={over[int(len(over) * 0.95) - 1]:.2f}ms max={over[-1]:.2f}ms")
        both = []
        for _ in range(20):
            begin = time.perf_counter()
            viewport.repaint()
            window._rx_shot.repaint()
            both.append((time.perf_counter() - begin) * 1000.0)
        both.sort()
        say(f"  父重绘 + 覆盖层重绘 合计：P50={statistics.median(both):.2f}ms  "
            f"→ 与实测帧间隔 "
            f"{12.6:.1f}ms（P50）对比即可看赔付比例")

        # 决定性对照：覆盖层重绘成本，flags 关/开
        window._rx_shot.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        off = []
        for _ in range(20):
            begin = time.perf_counter()
            window._rx_shot.repaint()
            off.append((time.perf_counter() - begin) * 1000.0)
        window._rx_shot.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        pump(app, 60)
        on = []
        for _ in range(20):
            begin = time.perf_counter()
            window._rx_shot.repaint()
            on.append((time.perf_counter() - begin) * 1000.0)
        window._rx_shot.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        say(f"  覆盖层自重绘  WA_OpaquePaintEvent=关 {statistics.median(off):.2f}ms  "
            f"/ 开 {statistics.median(on):.2f}ms  "
            f"→ 差 {statistics.median(off) - statistics.median(on):.2f}ms "
            f"= 半透明子控件强制父重绘的部分")
        window._rx_shot.hide()
        window._finish_rx_xfade()
        return 0
    finally:
        if window is not None:
            try:
                window.close()
            except RuntimeError:
                pass
        pump(app, 250)
        restore_settings(settings, snapshot)
        settings.sync()
        with open(OUT, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_lines))
        print(f"产物：{OUT}")


if __name__ == "__main__":
    sys.exit(main())
