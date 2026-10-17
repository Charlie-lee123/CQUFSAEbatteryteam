# -*- coding: utf-8 -*-
r"""QA 独立验收探针：动画帧率 + "无跳变"对抗测试。

自己写（不复用工程师的 ``_probe_fps.py``）：量 ``ui_anim`` 共享计时器的**每次滴答
间隔**（= 引擎实际帧间隔），对每个场景报告 P50 / P95 帧率、最大间隔、**>20ms 帧数**。

场景（任务验收点全盖）：
  ① 按钮悬停  ② 按钮按下  ③ 下拉弹层展开  ④ 接收区最大化
  ⑤ 退出最大化 ⑥ 设置面板里拨动开关  ⑦ 滚动跟随

对抗测试（重点）：
  A. 最大化动画**中途反向**（点第二次）—— 逐帧记关键尺寸，要求不出现阶跃；
  B. 按下反馈中途移开鼠标 —— 逐帧记 press 值，要求平滑衰减不跳；
  C. 滚动跟随中途打断（再次追底）—— 逐帧记滚动值，要求平滑不跳。

用法（**每轮重启进程**，跑 3 轮）：
    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools\_qa_fps_check.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QEvent, QObject, QPointF, QSettings, Qt   # noqa: E402
from PySide6.QtGui import QMouseEvent                                # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

from ui_theme import apply_scheme, ui_font                           # noqa: E402

FRAME_BUDGET_MS = 20.0
P95_TARGET = 120.0


class _TickClock(QObject):
    def __init__(self) -> None:
        super().__init__()
        self._times: list[float] = []
        self._on = False

    def attach(self) -> None:
        from ui_anim import _ticker
        _ticker()._timer.timeout.connect(self._on_tick)

    def _on_tick(self) -> None:
        if self._on:
            self._times.append(time.perf_counter())

    def start(self) -> None:
        self._times = []
        self._on = True

    def finish(self) -> list[float]:
        self._on = False
        return self._times


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    i = int(round(fraction * (len(sorted_values) - 1)))
    i = max(0, min(len(sorted_values) - 1, i))
    return sorted_values[i]


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0)


def measure(app, clock, trigger, max_seconds: float = 2.5) -> dict:
    """量一段动画期间的引擎帧间隔。"""
    from ui_anim import ticker_active

    clock.start()
    trigger()
    pump(app, 0.05)                     # 有些动画延后一拍起跑
    deadline = time.perf_counter() + max_seconds
    while ticker_active() and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0)
    times = clock.finish()
    intervals = [(b - a) * 1000.0 for a, b in zip(times, times[1:]) if b > a]
    if not intervals:
        return {"frames": len(times), "p50": 0.0, "p95": 0.0, "max": 0.0, "over": 0}
    fps = sorted(1000.0 / ms for ms in intervals)
    return {
        "frames": len(times),
        "p50": 1000.0 / _percentile(sorted(intervals), 0.50),
        "p95": _percentile(fps, 0.95),
        "max": max(intervals),
        "over": sum(1 for ms in intervals if ms > FRAME_BUDGET_MS),
    }


# --------------------------------------------------------------------- 场景

def _press_event(widget) -> QMouseEvent:
    pos = QPointF(widget.rect().center())
    return QMouseEvent(QEvent.Type.MouseButtonPress, pos, pos,
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


def run_scenarios(app, window, clock) -> list[tuple[str, dict]]:
    phases: list[tuple[str, dict]] = []
    button = window.btn_expand
    combo = window.cmb_baud

    # 热身（不计入统计）：第一次弹层会现建窗口、第一次展开会现建 Reveal
    app.sendEvent(button, QEvent(QEvent.Type.Enter))
    pump(app, 0.4)
    app.sendEvent(button, QEvent(QEvent.Type.Leave))
    pump(app, 0.3)
    combo.showPopup()
    pump(app, 0.4)
    combo.hidePopup()
    pump(app, 0.3)
    window._expand_receive()
    pump(app, 1.0)
    window._collapse_receive()
    pump(app, 1.0)

    # ① 按钮悬停
    phases.append(("按钮悬停", measure(app, clock,
                    lambda: app.sendEvent(button, QEvent(QEvent.Type.Enter)))))
    app.sendEvent(button, QEvent(QEvent.Type.Leave))
    pump(app, 0.4)

    # ② 按钮按下
    phases.append(("按钮按下反馈", measure(app, clock,
                    lambda: button.mousePressEvent(_press_event(button)))))
    button.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(button.rect().center()),
        QPointF(button.rect().center()), Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    app.sendEvent(button, QEvent(QEvent.Type.Leave))
    pump(app, 0.4)

    # ③ 下拉弹层
    phases.append(("下拉弹层展开", measure(app, clock, combo.showPopup)))
    combo.hidePopup()
    pump(app, 0.4)

    # ④ 接收区最大化
    phases.append(("接收区最大化", measure(app, clock, window._expand_receive)))
    pump(app, 0.6)
    # ⑤ 退出最大化
    phases.append(("退出最大化", measure(app, clock, window._collapse_receive)))
    pump(app, 0.6)

    # ⑥ 设置面板里拨动开关（Switch 的旋钮补间）
    from ui_settings import SettingsDialog
    from main_window import BUTTON_SPECS
    dialog = SettingsDialog("dark", 0, BUTTON_SPECS, [], parent=window)
    dialog.show()
    pump(app, 0.3)
    switch = dialog._button_switches.get(next(iter(dialog._button_switches), ""))
    if switch is not None:
        phases.append(("设置面板·拨动开关",
                       measure(app, clock, lambda: switch.setChecked(not switch.isChecked()))))
    else:
        phases.append(("设置面板·展开主题下拉",
                       measure(app, clock, dialog.cmb_theme.showPopup)))
    pump(app, 0.4)
    dialog.close()
    dialog.deleteLater()
    pump(app, 0.3)

    # ⑦ 滚动跟随（先在接收区堆数据，把滚动条压回顶部再追底）
    window.txt_rx.clear()
    for i in range(400):
        window.txt_rx.appendPlainText(f"2026-10-17 00:00:{i % 60:02d}.000  电压第 {i} 行")
    pump(app, 0.2)
    window.txt_rx.verticalScrollBar().setValue(0)
    pump(app, 0.05)
    phases.append(("滚动跟随", measure(app, clock, window._follow_rx_bottom)))
    pump(app, 0.4)
    return phases


# --------------------------------------------------------------------- 对抗

def _max_step(seq) -> float:
    return max((abs(b - a) for a, b in zip(seq, seq[1:])), default=0.0)


def adversarial_maximize_reverse(app, window) -> tuple[bool, str]:
    """最大化动画跑到一半时反向 —— 逐帧记**动画量**（Reveal.progress），要求无阶跃。

    ⚠️ 窗口本身从普通态跳到全屏是 OS 的瞬时行为（既定的），这里量的是
    **被动画的块**（页头 / 配置卡）的连续位移。
    """
    # 回到"普通窗口 + 两块都展开"的干净起点（否则头可能是隐藏态、progress 停在 0）
    if window._expanded:
        window._collapse_receive()
    window._want_fullscreen = False
    window._expect_normal = True
    if window.isFullScreen():
        window.showNormal()
    window.setGeometry(60, 40, 1180, 860)
    pump(app, 0.8)
    window.brand_header.show()
    window._config_card.show()
    rev_h = window._block_reveal(window.brand_header, False)     # 页头：竖向
    rev_w = window._block_reveal(window._config_card, True)      # 配置卡：横向
    rev_h.mark_expanded()
    rev_w.mark_expanded()
    pump(app, 0.3)
    start_ok = rev_h.progress > 0.99 and rev_w.progress > 0.99

    # ⚠️ 逐**引擎帧**采样（挂在 ticker 上），而不是每 3ms 轮询 —— 后者会一次跳过
    #    多个帧、把真实逐帧位移虚报成"阶跃"（曾报 0.40，实为采样的混叠）。
    from ui_anim import _ticker

    hs, ws, ts = [], [], []
    state = {"reversed": False}

    def on_tick() -> None:
        hs.append(rev_h.progress)
        ws.append(rev_w.progress)
        ts.append(time.perf_counter())
        if not state["reversed"] and rev_h.progress < 0.5:   # 跑到一半
            window._collapse_receive()                       # 中途反向
            state["reversed"] = True

    _ticker()._timer.timeout.connect(on_tick)
    try:
        window._expand_receive()
        end = time.perf_counter() + 2.0
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0)
            if (state["reversed"] and rev_h.progress >= 0.999
                    and rev_w.progress >= 0.999):
                break
    finally:
        _ticker()._timer.timeout.disconnect(on_tick)
    reversed_ = state["reversed"]

    h_step, w_step = _max_step(hs), _max_step(ws)

    def p95_slope(seq):
        """归一化速度的 95 分位（抗单次调度抖动）：位移 /（该帧真实时长/220ms）。

        in_out_quint 的理论最大斜率是 5.0（progress / 归一化时间）；真正的一次性
        几何跳变（未插值的 setGeometry/setMaximumHeight）会给出几十上百的值。
        """
        speeds = sorted(abs(b - a) / ((tb - ta) / 0.220)
                        for a, b, ta, tb in zip(seq, seq[1:], ts, ts[1:]) if tb > ta)
        if not speeds:
            return 0.0
        return speeds[min(len(speeds) - 1, int(round(0.95 * (len(speeds) - 1))))]

    h_slope, w_slope = p95_slope(hs), p95_slope(ws)
    # 反向只允许发生一次（不来回抖）
    signs = [1 if b > a + 1e-6 else -1 if b < a - 1e-6 else 0
             for a, b in zip(hs, hs[1:])]
    flips = sum(1 for a, b in zip(signs, signs[1:])
                if a != 0 and b != 0 and a != b)
    # 判据：起手是展开态、中途确实反向、逐帧位移 ≤0.45×满量程（真跳变会 ≥0.5）、
    # 归一化速度 p95 ≤ 6（≈曲线理论上限 5）、方向只翻转一次。
    ok = (start_ok and reversed_ and max(h_step, w_step) <= 0.45
          and h_slope <= 6.0 and w_slope <= 6.0 and flips <= 2)
    detail = (f"起手展开={start_ok} 中途反向={reversed_}；单帧位移≤{max(h_step, w_step):.3f}；"
              f"归一化速度 p95 页头={h_slope:.2f} 配置卡={w_slope:.2f}（理论上限 5）；"
              f"方向翻转 {flips} 次；采样 {len(hs)}")
    return ok, detail


def adversarial_press_leave(app, window) -> tuple[bool, str]:
    """按下反馈动画中途移开鼠标 —— 逐帧记 press 值，要求平滑衰减不跳。"""
    button = window.btn_expand
    button._press = 0.0
    button.mousePressEvent(_press_event(button))
    pump(app, 0.05)                       # 跑到一半
    app.sendEvent(button, QEvent(QEvent.Type.Leave))
    values = []
    end = time.perf_counter() + 0.6
    while time.perf_counter() < end:
        app.processEvents()
        values.append(button._press)
        time.sleep(0.003)
    step = _max_step(values)
    dropped = max((a - b) for a, b in zip(values, values[1:])) if len(values) > 1 else 0
    ok = step <= 0.30                       # 单帧跳变不得过大
    return ok, (f"press 峰值={max(values):.2f} 单步最大={step:.3f} "
                f"单帧最大跌幅={dropped:.3f} 采样 {len(values)} 点")


def adversarial_scroll_interrupt(app, window) -> tuple[bool, str]:
    """滚动跟随中途打断：跑到一半**再次追底**（重新定向）—— 要求平滑、不回跳。

    ⚠️ 不在采样期直接 appendPlainText：QPlainTextEdit 光标在末尾时会**自行**跳到底，
    那是控件行为、不是本项目的滚动补间（曾把单帧跳变虚报成 50+px）。
    """
    window.txt_rx.clear()
    for i in range(300):
        window.txt_rx.appendPlainText(f"行 {i} 电压 3700 mV")
    pump(app, 0.2)
    bar = window.txt_rx.verticalScrollBar()
    bar.setValue(0)
    pump(app, 0.05)
    window._follow_rx_bottom()
    values = []
    retargeted = False
    end = time.perf_counter() + 0.7
    while time.perf_counter() < end:
        app.processEvents()
        values.append(bar.value())
        if not retargeted and len(values) >= 6:
            window._follow_rx_bottom()     # 中途打断并重新追底
            retargeted = True
        time.sleep(0.004)
    step = _max_step(values)
    drop = max((a - b) for a, b in zip(values, values[1:])) if len(values) > 1 else 0
    span = max(values) - min(values) if values else 1
    ok = retargeted and drop <= 0 and step <= 0.30 * max(1, span)
    return ok, (f"滚动 {min(values)}..{max(values)} 单步最大={step} "
                f"最大回跳={drop} 跨度={span} 采样 {len(values)}")


# --------------------------------------------------------------------- main

def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    from ui_anim import set_animations_enabled_override
    set_animations_enabled_override(True)

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    import customization
    for spec in customization.specs():
        customization.set(spec["key"], spec["default"])

    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    from main_window import MainWindow
    window = MainWindow()
    window._port_watch.stop()
    screen = app.primaryScreen().availableGeometry()
    window.setGeometry(screen.x() + 40, screen.y() + 30, 1180, 860)
    window.show()
    window.raise_()
    pump(app, 0.8)

    clock = _TickClock()
    clock.attach()

    phases = run_scenarios(app, window, clock)

    print("=" * 74)
    print("帧率实测（ui_anim 引擎滴答间隔 → 帧率；仅统计动画在跑的那段）")
    print("=" * 74)
    all_ok = True
    for name, st in phases:
        ok = st["p95"] >= P95_TARGET and st["over"] == 0
        if st["frames"] == 0:
            verdict = "无动画帧"
        else:
            all_ok = all_ok and ok
            verdict = "PASS" if ok else "FAIL"
        print(f"[{name}] 帧数={st['frames']:3d}  P50={st['p50']:6.1f}fps  "
              f"P95={st['p95']:6.1f}fps  max={st['max']:6.2f}ms  "
              f">20ms={st['over']}  -> {verdict}")

    print("-" * 74)
    print("对抗测试（无跳变）")
    print("-" * 74)
    for label, fn in (("最大化中途反向", adversarial_maximize_reverse),
                      ("按下中途移开鼠标", adversarial_press_leave),
                      ("滚动跟随中途打断", adversarial_scroll_interrupt)):
        ok, detail = fn(app, window)
        all_ok = all_ok and ok
        print(f"[{label}] {'PASS' if ok else 'FAIL'}  {detail}")

    print("=" * 74)
    print(f"ROUND RESULT: {'PASS' if all_ok else 'FAIL'}")
    print("=" * 74)

    window.setGeometry(0, 0, 1134, 820)
    window.close()
    # 把本次探针**新造出来的键**也清掉（否则会永久留在用户配置里）
    for key in set(settings.allKeys()) - set(snapshot):
        settings.remove(key)
    for key, value in snapshot.items():
        if value is not None:
            settings.setValue(key, value)
    settings.sync()
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
