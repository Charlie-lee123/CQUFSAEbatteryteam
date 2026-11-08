# -*- coding: utf-8 -*-
r"""探针：设置面板开合 / 发送区收展 / 接收区最大化还原 —— 逐帧连续性 + 帧率。

三段交互共用一台采样器（挂 ``ui_anim._Ticker._tick``）：补间推进一拍就取一帧，
与动画严格同拍；每段前先取一帧**动作前基线**（重建/显隐是同步完成的，没有基线
就会把硬切看成"没变化"），段后统计：
  · 每条几何轨迹的单帧最大位移 / 异常帧（>12px）/ 可见性翻转
  · 帧间隔 P50 / P95 / max、掉帧数（>1.5×名义）
  · 补间节流的**设计上限**（min_interval_ms 决定的帧率天花板）

用法：.venv\Scripts\python.exe devtools/_probe_panel_frames.py
产物：_check/panel_frames.txt
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

from PySide6.QtCore import QPoint, QSettings, Qt, QTimer              # noqa: E402
from PySide6.QtWidgets import QApplication                            # noqa: E402

CHECK_DIR = os.path.join(_ROOT, "_check")
OUT = os.path.join(CHECK_DIR, "panel_frames.txt")
_lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    _lines.append(text)


def pump(app, ms: float) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


class Tap:
    """按补间节拍取样：每条轨迹 = (x, y, w, h) 或 None（不可见/已销毁）。"""

    def __init__(self, app, tracks: dict) -> None:
        self.app = app
        self.tracks = tracks            # name -> callable() -> QWidget | None
        self.frames: list[tuple[float, dict]] = []
        self.ticks: list[float] = []
        self.deliveries: list[float] = []
        self.t0 = 0.0
        self.action_ms = 0.0
        self._orig = None
        self._orig_deliver = None

    def _snap(self, t: float) -> None:
        items = {}
        for name, getter in self.tracks.items():
            try:
                widget = getter()
            except (RuntimeError, KeyError):
                widget = None
            if widget is None:
                items[name] = None
                continue
            try:
                if not widget.isVisible():
                    items[name] = None
                    continue
                pos = widget.mapToGlobal(QPoint(0, 0))
                items[name] = (pos.x(), pos.y(), widget.width(), widget.height())
            except RuntimeError:
                items[name] = None
        self.frames.append((t, items))

    def __enter__(self):
        import ui_anim
        ticker = ui_anim._ticker()
        self._orig = ticker._tick

        def _patched() -> None:
            self._orig()
            self.ticks.append(time.perf_counter())
            self._snap((time.perf_counter() - self.t0) * 1000.0)

        ticker._tick = _patched
        # ⛔ 光看 _tick 会把"被节流的补间"高估成满帧率：``Tween(min_interval_ms=16)``
        #    的计时器照样每 6ms 响一次，但**不投递值**（见 ui_anim.Tween.advance）。
        #    所以真正的"画面更新帧率"必须数 _deliver。
        self._orig_deliver = ui_anim.Tween._deliver
        original_deliver = self._orig_deliver

        def _deliver(tween, value) -> None:
            original_deliver(tween, value)
            self.deliveries.append((id(tween), time.perf_counter()))

        ui_anim.Tween._deliver = _deliver
        return self

    def __exit__(self, *exc) -> bool:
        import ui_anim
        ui_anim._ticker()._tick = self._orig
        if self._orig_deliver is not None:
            ui_anim.Tween._deliver = self._orig_deliver
        return False

    def run(self, action, settle_ms: float = 900.0) -> dict:
        self.frames.clear()
        self.ticks.clear()
        self.deliveries.clear()
        self.t0 = time.perf_counter()
        self._snap(-1.0)                       # 动作前基线
        begin = time.perf_counter()
        action()
        self.action_ms = (time.perf_counter() - begin) * 1000.0
        deadline = time.perf_counter() + settle_ms / 1000.0
        while time.perf_counter() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.app.processEvents()
        return {"frames": list(self.frames), "ticks": list(self.ticks),
                "deliveries": list(self.deliveries),
                "action_ms": self.action_ms}


def analyse(title: str, rec: dict, hz: int, note: str = "",
            tween=None, tween_name: str = "") -> None:
    frames = rec["frames"]
    say(f"\n---- {title} ----")
    say(f"  action() 同步耗时 {rec['action_ms']:.2f}ms；采样 {len(frames) - 1} 帧"
        + (f"；{note}" if note else ""))
    for name in frames[0][1]:
        deltas = []
        flips = []
        prev = None
        prev_vis = None
        for t, items in frames:
            cur = items.get(name)
            vis = cur is not None
            if prev_vis is not None and vis != prev_vis:
                flips.append(round(t, 1))
            prev_vis = vis
            if cur is not None and prev is not None:
                deltas.append((max(abs(cur[i] - prev[i]) for i in range(4)), t, prev, cur))
            prev = cur
        if not deltas:
            say(f"  {name:<11} 无位移数据（{len(frames)} 帧里都不可见）")
            continue
        top = max(deltas, key=lambda item: item[0])
        big = [d for d in deltas if d[0] > 12]
        flag = "  ⚠" if (big or flips) else ""
        say(f"  {name:<11} 最大单帧 {top[0]:6.1f}px @ t={top[1]:6.1f}ms  "
            f"异常帧 {len(big):>2}  显隐翻转 {flips if flips else 0}{flag}")
        if top[0] > 12:
            say(f"      {top[2]} → {top[3]}")
    ticks = rec["ticks"]
    if len(ticks) > 2:
        intervals = sorted((b - a) * 1000.0 for a, b in zip(ticks, ticks[1:]))
        live = [iv for iv in intervals if iv < 40.0]
        nominal = 1000.0 / hz
        if live:
            p50 = statistics.median(live)
            drops = sum(1 for iv in live if iv > 1.5 * nominal)
            say(f"  计时器节拍 P50={p50:.2f}ms({1000.0 / p50:.1f}fps)  "
                f"P95={live[int(len(live) * 0.95) - 1]:.2f}ms  最大={live[-1]:.2f}ms  "
                f"掉帧={drops}/{len(live)}（名义 {nominal:.2f}ms）")
    deliv = rec.get("deliveries") or []
    if tween is not None:
        # 只统计**这一段补间**投递了哪些帧：面板里同时活着的 Tween 很多
        # （Switch/滑杆/Reveal），不按实例过滤会把别人起的补间算进来。
        target_id = id(tween)
        deliv = [t for tid, t in deliv if tid == target_id]
    else:
        deliv = [t for _tid, t in deliv]
    if len(deliv) > 2:
        intervals = sorted((b - a) * 1000.0 for a, b in zip(deliv, deliv[1:]))
        live = [iv for iv in intervals if iv < 200.0]
        if live:
            p50 = statistics.median(live)
            say(f"  ★ 实际投递（画面更新）P50={p50:.2f}ms({1000.0 / p50:.1f}fps)  "
                f"P95={live[int(len(live) * 0.95) - 1]:.2f}ms  最大={live[-1]:.2f}ms  "
                f"帧数={len(deliv)}"
                + (f"  [{tween_name}]" if tween_name else ""))


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
        pump(app, 800)
        say(f"=== 环境 ===  帧率 {hz}Hz（名义 {1000.0 / hz:.2f}ms） "
            f"窗口 {window.width()}x{window.height()} "
            f"expanded={window._expanded} tx_collapsed={window._tx_collapsed}")

        tracks = {
            "brand": lambda: window.brand_header,
            "config": lambda: window._config_card,
            "tx_card": lambda: window._tx_card,
            "tx_content": lambda: window._tx_content,
            "rail": lambda: window._rx_rail,
            "opts": lambda: window._rx_opts_row,
            "rx_card": lambda: window._rx_card,
            "open_row": lambda: window._open_row,
            "settings_shot": lambda: window._settings_shot,
            "dialog": lambda: window._settings_dialog,
        }

        with Tap(app, tracks) as tap:
            # ---- B1 设置面板：开 → 关 ----
            rec = tap.run(window._open_settings, settle_ms=1100)
            dialog = window._settings_dialog
            analyse("B1a 打开设置面板（_SettingsShot 从设置按钮放大）", rec, hz,
                    f"面板 {'已开' if dialog is not None else '没开'}",
                    tween=window._settings_geom_tween, tween_name="_settings_geom_tween")
            first = [round(t, 1) for t, _ in rec["frames"][:4]]
            say(f"  帧时刻前 4 个（t=0 是 action 返回/补间起跑）：{first}")
            say(f"  首帧延迟 = {first[1] - rec['action_ms'] if len(first) > 1 else 0:.1f}ms"
                f"（补间目标间隔 {1000.0 / hz:.2f}ms → 前 "
                f"{max(0, (first[1] - rec['action_ms']) / (1000.0 / hz)):.0f} 帧被吞）")
            if dialog is not None:
                say(f"  快照窗口几何 = {window._settings_shot.geometry()}  "
                    f"目标 = {window._settings_geom_to}")
                shot_costs = []
                for _ in range(10):
                    begin = time.perf_counter()
                    window._settings_shot.repaint()
                    shot_costs.append((time.perf_counter() - begin) * 1000.0)
                shot_costs.sort()
                say(f"  _settings_shot.repaint()（整块位图贴一次）P50="
                    f"{statistics.median(shot_costs):.2f}ms  "
                    f"max={shot_costs[-1]:.2f}ms")
            pump(app, 400)
            rec = tap.run(lambda: window._close_settings_dialog(accepted=False),
                          settle_ms=1200)
            analyse("B1b 关闭设置面板（缩回设置按钮）", rec, hz,
                    tween=window._settings_geom_tween, tween_name="_settings_geom_tween")

            # ---- B2 发送区：收起 → 展开 ----
            if window._expanded:
                window._collapse_receive()
                pump(app, 900)
            window._set_send_collapsed(False)
            pump(app, 600)
            say(f"\n  发送区起点：collapsed={window._tx_collapsed} "
                f"cardH={window._tx_card.height()} sizes={window._splitter.sizes()}")
            rec = tap.run(lambda: window._set_send_collapsed(True), settle_ms=900)
            analyse("B2a 发送区收起（TX_ANIM_MS 补间 + 结束钉高）", rec, hz,
                    tween=window._split_tween, tween_name="_split_tween")
            say(f"  收完：cardH={window._tx_card.height()} "
                f"sizes={window._splitter.sizes()} "
                f"content可见={window._tx_content.isVisible()}")
            pump(app, 300)
            rec = tap.run(lambda: window._set_send_collapsed(False), settle_ms=900)
            analyse("B2b 发送区展开", rec, hz,
                    tween=window._split_tween, tween_name="_split_tween")
            say(f"  展开后：cardH={window._tx_card.height()} "
                f"sizes={window._splitter.sizes()} "
                f"content可见={window._tx_content.isVisible()}")

            # ---- B3 接收区最大化 / 还原 ----
            pump(app, 400)
            rec = tap.run(window._expand_receive, settle_ms=1400)
            analyse("B3a 接收区最大化（_rx_transition，min_interval=16ms）", rec, hz,
                    f"expanded={window._expanded} fullscreen={window.isFullScreen()}",
                    tween=window._rx_tween, tween_name="_rx_tween")
            tween = window._rx_tween
            if tween is not None:
                say(f"  补间节流 min_interval_ms={tween._min_interval * 1000:.0f}ms "
                    f"→ 设计上限 {1000.0 / max(1e-6, tween._min_interval * 1000):.1f}fps")
            pump(app, 400)
            rec = tap.run(window._collapse_receive, settle_ms=1400)
            analyse("B3b 接收区还原", rec, hz,
                    f"expanded={window._expanded} fullscreen={window.isFullScreen()}",
                    tween=window._rx_tween, tween_name="_rx_tween")
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
