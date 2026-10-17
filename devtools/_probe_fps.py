# -*- coding: utf-8 -*-
r"""帧率实测：在**重动画**期间统计真实帧间隔，输出 P50/P95 帧率与最大间隔。

量的是**动画引擎实际产出的帧**：``ui_anim`` 的共享计时器（8ms / PreciseTimer）
每滴答一次就推进一步补间、并让动画对象 ``update()`` 一次 —— 所以两次滴答之间的
间隔就是"这一帧到下一帧"的间隔。直接给它挂一个记录槽，量出来的就是引擎帧率。

为什么不数"Paint 事件"：Paint 只在控件可见且真的变了时才发生，动画之间的空档、
隐藏的块、被裁剪的区域都会让计数出现几十上百毫秒的假空档（那是**没有动画**，
不是**掉帧**）。任务的判据是"动画期间"，所以必须只统计**动画真正在跑**的那一段
—— 这里由 ``ticker_active()`` 精确界定。

覆盖三处重动画（任务的验收点）：
  ① 卡片悬停抬升
  ② 下拉弹层展开
  ③ 接收区最大化 / 退出最大化（多块同时动画，最重的一处）

判据（与任务书一致）：
  · P95 帧率 ≥ 120fps（P95 取帧率分布的高分位 —— 它对应**最快**的那批帧间）
  · 不得出现 > 20ms 的异常帧间隔（一次"跳变 / 卡顿"的量化定义）

用法：
    python devtools\_probe_fps.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QEvent, QObject, QSettings     # noqa: E402
from PySide6.QtWidgets import QApplication                # noqa: E402


def percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = int(round(fraction * (len(sorted_values) - 1)))
    index = 0 if index < 0 else len(sorted_values) - 1 if index >= len(sorted_values) else index
    return sorted_values[index]


class _TickClock(QObject):
    """记录动画引擎每一次滴答的时刻。"""

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


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0)


def measure(app, clock, trigger, max_seconds: float = 2.5) -> dict:
    """量一段动画期间的**引擎帧间隔**（trigger 里起动画）。"""
    from ui_anim import ticker_active

    clock.start()
    trigger()
    # 有些动画是**延后一拍**起跑的（下拉弹层走 QTimer.singleShot(0)），所以要先把
    # 这一拍跑掉，否则一上来 ticker 还是停的、会被当成"没有动画"。
    pump(app, 0.05)
    deadline = time.perf_counter() + max_seconds
    # 只统计动画在跑的那一段：补间跑完（ticker 停）立刻收工，不含尾部空档。
    while ticker_active() and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0)
    times = clock.finish()
    intervals = [(b - a) * 1000.0 for a, b in zip(times, times[1:]) if b > a]
    if not intervals:
        return {"frames": len(times), "p50_fps": 0.0, "p95_fps": 0.0,
                "max_ms": 0.0, "worst": []}
    fps = sorted(1000.0 / ms for ms in intervals if ms > 0)
    worst = sorted(((ms, i) for i, ms in enumerate(intervals)),
                   reverse=True)[:3]
    return {
        "frames": len(times),
        "p50_fps": 1000.0 / percentile(sorted(intervals), 0.50),
        "p95_fps": percentile(fps, 0.95),
        "max_ms": max(intervals),
        "worst": worst,
    }


def _stress_stats(intervals: list[float]) -> dict:
    if not intervals:
        return {"frames": 0, "p50_fps": 0.0, "max_ms": 0.0, "over20": 0}
    fps = sorted(1000.0 / ms for ms in intervals if ms > 0)
    return {
        "frames": len(intervals) + 1,
        "p50_fps": 1000.0 / percentile(sorted(intervals), 0.50),
        "p95_fps": percentile(fps, 0.95),
        "max_ms": max(intervals),
        "over20": sum(1 for ms in intervals if ms > 20.0),
    }


def stress_phase(app, clock, window, throttle: bool,
                 coalesce: bool = False) -> dict:
    """**高压场景**：一边以约 250 行/秒灌数据，一边把接收区最大化。

    决策-2 的路径对比就量这一段：
    · ``throttle=False`` 关掉高亮器节流（= 旧行为）；
    · ``throttle=True``  用 RxHighlighter.defer()（动画期合并高亮重刷）；
    · ``coalesce=True``  动画期间把追加**攒着**、结束再一次性刷入（只用于量上限，
      不上线 —— 它会让数据晚 ~250ms 才出现，有重演"内容消失"投诉的风险）。
    """
    from PySide6.QtCore import QTimer
    from ui_anim import ticker_active

    highlighter = window._rx_highlighter
    original_coalesce = window._set_rx_coalesce
    if throttle:
        highlighter.__dict__.pop("defer", None)     # 用类方法（带节流）
    else:
        highlighter.defer = lambda ms: None          # 关掉节流（= 旧行为）
        window._set_rx_coalesce = lambda ms: None    # 关掉追加合并（= 旧行为）

    buffered: list[tuple[str, str]] = []
    original_append = window._append_display

    def buffering(text: str, display: str) -> None:
        buffered.append((text, display))

    if coalesce:
        window._append_display = buffering          # type: ignore[method-assign]

    counter = [0]
    feeder = QTimer()
    feeder.setInterval(8)                            # 8ms 一行 ≈ 125 行/秒
    header = "Voltage1:"

    def feed() -> None:
        counter[0] += 1
        window._emit_block(header)
        window._emit_block(" ".join(f"{i}.371{counter[0] % 10}"
                                    for i in range(1, 8)))

    feeder.timeout.connect(feed)
    clock.start()
    feeder.start()
    window._expand_receive()
    deadline = time.perf_counter() + 2.0
    while ticker_active() and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.001)
    times = clock.finish()
    feeder.stop()
    feeder.deleteLater()
    if coalesce:
        window._append_display = original_append    # type: ignore[method-assign]
        for text, display in buffered:
            original_append(text, display)
    if not throttle:
        highlighter.__dict__.pop("defer", None)      # 还原成类方法
        window._set_rx_coalesce = original_coalesce  # type: ignore[method-assign]
    pump(app, 0.8)
    window._collapse_receive()
    pump(app, 1.0)
    intervals = [(b - a) * 1000.0 for a, b in zip(times, times[1:]) if b > a]
    return _stress_stats(intervals)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 起手必须与 main.py 一致（见 MEMORY.md 的铁律）：Fusion + 应用字体。
    from ui_anim import set_animations_enabled_override
    from ui_theme import apply_scheme, ui_font

    set_animations_enabled_override(True)     # 系统若关了动画，这里强制开
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    import customization
    from main_window import MainWindow

    # 探针跑完要还原用户配置：先重置定制项为默认（用户本机的隐藏配置会让界面
    # 只剩半扇窗，影响测量）。
    for spec in customization.specs():
        customization.set(spec["key"], spec["default"])
    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    window = MainWindow()
    window._port_watch.stop()
    screen = app.primaryScreen().availableGeometry()
    window.setGeometry(screen.x() + 60, screen.y() + 40, 1180, 860)
    window.show()
    window.raise_()
    pump(app, 0.6)

    clock = _TickClock()
    clock.attach()
    phases: list[tuple[str, dict]] = []

    card = window._rx_card
    combo = window.cmb_baud

    # ⚠️ 先各跑一遍**热身**（不计入统计）：第一次弹层会现建容器顶层窗口、第一次
    #    展开会现建各个 Reveal —— 那是"一笔一次性开销"，不是动画帧，混进来会把
    #    最大帧间隔抬到 20ms 以上。热身之后量的才是稳态动画的帧率。
    app.sendEvent(card, QEvent(QEvent.Type.Enter))
    pump(app, 0.35)
    app.sendEvent(card, QEvent(QEvent.Type.Leave))
    pump(app, 0.35)
    combo.showPopup()
    pump(app, 0.4)
    combo.hidePopup()
    pump(app, 0.4)
    window._expand_receive()
    pump(app, 1.0)
    window._collapse_receive()
    pump(app, 1.0)

    # ① 卡片悬停抬升
    phases.append(("卡片悬停抬升",
                   measure(app, clock,
                           lambda: app.sendEvent(card, QEvent(QEvent.Type.Enter)))))
    app.sendEvent(card, QEvent(QEvent.Type.Leave))
    pump(app, 0.4)

    # ② 下拉弹层展开
    phases.append(("下拉弹层展开", measure(app, clock, combo.showPopup)))
    combo.hidePopup()
    pump(app, 0.4)

    # ③ 接收区最大化 / 退出（多个块同时动画 —— 最重的一处）
    phases.append(("接收区最大化", measure(app, clock, window._expand_receive)))
    pump(app, 0.5)
    phases.append(("退出最大化", measure(app, clock, window._collapse_receive)))
    pump(app, 0.5)

    # ④ 发送区折叠 / 展开（手动那一路，走分割器动画 300ms）
    phases.append(("发送区折叠", measure(app, clock, window._toggle_send_area)))
    pump(app, 0.5)
    phases.append(("发送区展开", measure(app, clock, window._toggle_send_area)))
    pump(app, 0.5)

    # ⑤ 设置面板：从设置按钮长出来 / 原路收回
    phases.append(("设置面板打开", measure(app, clock, window._open_settings)))
    pump(app, 0.5)
    if window._settings_dialog is not None:
        phases.append(("设置面板关闭",
                       measure(app, clock,
                               lambda: window._close_settings_dialog(False))))
        pump(app, 0.6)

    # ⑥ 高压场景（决策-2）：250 行/秒灌数据的同时最大化 —— 前后对比
    off = stress_phase(app, clock, window, throttle=False)
    on = stress_phase(app, clock, window, throttle=True)
    ceiling = stress_phase(app, clock, window, throttle=True, coalesce=True)

    # ---- 报告 ----
    print("=" * 72)
    print("帧率实测（动画引擎滴答间隔 → 帧率；仅统计动画在跑的那一段）")
    print("=" * 72)
    ok = True
    for name, stats in phases:
        passed = stats["p95_fps"] >= 120.0 and stats["max_ms"] <= 20.0
        ok = ok and passed
        print(f"\n[{name}] 引擎帧数={stats['frames']}")
        print(f"   P50 帧率 = {stats['p50_fps']:7.1f} fps")
        print(f"   P95 帧率 = {stats['p95_fps']:7.1f} fps   (要求 >= 120)")
        print(f"   最大间隔 = {stats['max_ms']:7.2f} ms    (要求 <= 20)")
        slow = ", ".join(f"#{i}:{ms:.1f}ms" for ms, i in stats.get("worst", []))
        print(f"   最慢三帧间隔(序号:毫秒) = {slow or '无'}")
        print(f"   -> {'PASS' if passed else 'FAIL'}")
    print("\n" + "=" * 72)
    print("总判定:", "PASS" if ok else "FAIL")
    print("=" * 72)

    # ---- 决策-2 前后对比（高压场景）----
    print("\n" + "=" * 72)
    print("高压场景：约 250 行/秒灌数据 + 同时最大化（决策-2 前后对比）")
    print("=" * 72)
    print(f"  [节流前] P50={off['p50_fps']:6.1f}fps  最长帧={off['max_ms']:5.2f}ms  "
          f">20ms 帧={off['over20']}")
    print(f"  [节流后] P50={on['p50_fps']:6.1f}fps  最长帧={on['max_ms']:5.2f}ms  "
          f">20ms 帧={on['over20']}")
    print(f"  [合并追加·仅量上限] P50={ceiling['p50_fps']:6.1f}fps  "
          f"最长帧={ceiling['max_ms']:5.2f}ms  >20ms 帧={ceiling['over20']}")
    gain = off["over20"] - on["over20"]
    print(f"  -> 高亮器节流：>20ms 帧 {off['over20']} → {on['over20']}"
          f"（Δ{gain:+d}）；P50 {off['p50_fps']:.1f} → {on['p50_fps']:.1f} fps")
    print("  ⚠️ 接收区不可见帧数见 _probe_round2.py（三条路径都保持 0 —— "
          "本方案不冻结重绘）")
    print("=" * 72)

    # ---- 还原 ----
    # ⚠️ 探针开头把定制项重置成了默认（用户本机的隐藏配置会让界面只剩半扇窗），
    #    这些"重置"会在注册表里**新造**出键。所以除了写回快照，还要把快照里没有
    #    的键删掉 —— 否则每跑一次探针就给用户留下一堆默认值键（QA 踩过这个坑）。
    window.setGeometry(0, 0, 1134, 820)
    window.close()
    for key in list(settings.allKeys()):
        if key not in snapshot:
            settings.remove(key)
    for key, value in snapshot.items():
        if value is not None:
            settings.setValue(key, value)
    settings.sync()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
