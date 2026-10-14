"""统一动画引擎：固定 120fps、一律非线性缓动、所有补间共用一条计时器。

为什么自己写这一层，而不是继续用 ``QVariantAnimation`` / ``QPropertyAnimation``：
Qt 的动画节拍由它内部的统一计时器决定，不由我们定（Qt6 默认约 60fps）。在
120Hz 屏幕上，60fps 的过渡一眼能看出"两帧才动一次"的台阶。而这个界面里的动效
全是几十毫秒的短过渡，帧率不够就显脏 —— 所以节拍必须自己拿住。

- **节拍**：``FRAME_MS = 8ms``（125Hz），稳稳盖住 120fps。用 ``PreciseTimer``，
  普通精度的计时器会被系统拉到 15ms 左右，那就掉回 60fps 了。
- **曲线**：一律非线性（见 ``EASINGS``）。线性运动在 UI 上发死；短过渡必须
  "起步快、落地慢"才收得住。
- **进度**用 ``perf_counter`` 自己算，不信"计时器回调了几次"：某一帧被拖长时
  进度也不会走偏。
- **只在有动画时计时**：所有补间共用一条计时器，队列空了立刻停 —— 界面静止时
  一个计时器都不留。这跟"没有常驻动画循环"这条原则是一致的。

界面静止 = 零开销；界面在动 = 125Hz 连续推进。
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

# 目标帧率。120fps 是"高刷屏上看不出台阶"的门槛。
FPS = 120
# 节拍取 8ms（125Hz）。round(1000/120) = 8，所以实际刷新率 ≥ 目标帧率。
FRAME_MS = max(1, round(1000 / FPS))

# Qt 里 maximumHeight 的"无上限"取值。动画结束后要把上限放开，
# 否则控件会被永久钉在动画结束那一刻的高度上。
QWIDGETSIZE_MAX = 16_777_215


# ---------------------------------------------------------------- 缓动曲线
#
# 全部非线性。linear 只留给测试做对照，界面上不用。

def linear(t: float) -> float:
    return t


def out_cubic(t: float) -> float:
    """起步快、落地慢。短过渡的默认曲线。"""
    return 1.0 - (1.0 - t) ** 3


def out_quint(t: float) -> float:
    """比 out_cubic 更冲、收尾更长 —— 开关滑块这种小位移用它更利落。"""
    return 1.0 - (1.0 - t) ** 5


def in_out_cubic(t: float) -> float:
    """两头慢、中间快。整块内容的展开/收起用它，起止都不突兀。"""
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0


def out_expo(t: float) -> float:
    """极端的前快后慢，适合"瞬间响应、慢慢落定"的衰减。"""
    return 1.0 if t >= 1.0 else 1.0 - 2.0 ** (-10.0 * t)


EASINGS: dict[str, Callable[[float], float]] = {
    "linear": linear,
    "out_cubic": out_cubic,
    "out_quint": out_quint,
    "in_out_cubic": in_out_cubic,
    "out_expo": out_expo,
}


def _resolve(curve) -> Callable[[float], float]:
    if callable(curve):
        return curve
    return EASINGS.get(str(curve), out_cubic)


# ---------------------------------------------------------------- 补间

class Tween(QObject):
    """把一个值从 ``start`` 连续推到 ``end``，时长以毫秒计。

    调用方只关心"给我一串中间值"，不关心帧从哪来 —— 帧由共享计时器统一派发，
    所以一个界面上十个动画在跑也只有一条计时器。
    """

    def __init__(self, duration: int = 200, curve=out_cubic,
                 on_value: Callable[[float], None] | None = None,
                 on_finished: Callable[[], None] | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.duration = max(1, int(duration))
        self._curve = _resolve(curve)
        self._on_value = on_value
        self._on_finished = on_finished
        self._from = 0.0
        self._to = 1.0
        self._begin = 0.0
        self._running = False
        if parent is not None:
            # 宿主控件销毁时必须把自己从共享计时器上摘下来：计时器持有的是
            # **Python** 引用，宿主的 C++ 对象死了它并不知道，下一帧回调就会
            # 踩到已删除对象（真机踩过：设置面板销毁时 Switch 的滑块动画还在
            # 计时器里，一帧之后炸 RuntimeError，把正在跑的其它动画一起带崩）。
            # destroyed() 在宿主析构早期发出，这里只动 Python 侧状态，安全。
            parent.destroyed.connect(self.stop)

    # -- 对外 ------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    def start(self, start: float = 0.0, end: float = 1.0,
              duration: int | None = None) -> "Tween":
        """重新起一段。**可以随时打断**：起点就是当前值，不会跳一下再走。"""
        if duration is not None:
            self.duration = max(1, int(duration))
        self._from = float(start)
        self._to = float(end)
        self._begin = time.perf_counter()
        self._running = True
        _ticker().add(self)
        self._deliver(self._from)        # 立刻补一次起点，避免停在旧值上等第一帧
        return self

    def stop(self, finished: bool = False) -> None:
        if not self._running:
            return
        self._running = False
        _ticker().discard(self)
        if finished and self._on_finished is not None:
            self._on_finished()

    def advance(self, now: float) -> bool:
        """推进一步。返回 False 表示这段已经跑完。"""
        progress = (now - self._begin) / (self.duration / 1000.0)
        if progress >= 1.0:
            self._deliver(self._to)
            self.stop(finished=True)
            return False
        self._deliver(self._from + (self._to - self._from) * self._curve(progress))
        return True

    # -- 内部 ------------------------------------------------------------

    def _deliver(self, value: float) -> None:
        if self._on_value is not None:
            self._on_value(value)


class _Ticker(QObject):
    """补间调度器。队列空了自己停掉 —— 静止时不留任何计时器。"""

    def __init__(self) -> None:
        super().__init__()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._pending: list[Tween] = []

    def add(self, tween: Tween) -> None:
        if tween not in self._pending:
            self._pending.append(tween)
        if not self._timer.isActive():
            self._timer.start()

    def discard(self, tween: Tween) -> None:
        if tween in self._pending:
            self._pending.remove(tween)
        try:
            if not self._pending and self._timer.isActive():
                self._timer.stop()
        except RuntimeError:
            # 程序退出阶段 QApplication 先死、各窗口的 destroyed 信号随后触发
            # tween.stop()，此时计时器自己也可能已被销毁 —— 摘干净 Python 侧
            # 的引用就够了，别让收尾阶段炸出无谓的异常。
            pass

    def _tick(self) -> None:
        now = time.perf_counter()
        for tween in list(self._pending):
            tween.advance(now)


_ticker_instance: _Ticker | None = None


def _ticker() -> _Ticker:
    """惰性创建：要在 QApplication 之后才有计时器可谈。"""
    global _ticker_instance
    if _ticker_instance is None:
        _ticker_instance = _Ticker()
    return _ticker_instance


def ticker_active() -> bool:
    """当前有没有补间在跑。给测试用，也用来证明"静止时零开销"。"""
    return _ticker_instance is not None and _ticker_instance._timer.isActive()


def ticker_interval() -> int:
    """调度器实际使用的帧间隔（毫秒）。测试用 —— 帧数会受机器负载影响，
    节拍不会，所以"够不够 120fps"要断言在这里。"""
    return _ticker()._timer.interval()


# ---------------------------------------------------------------- 展开/收起

class Reveal(QObject):
    """让控件在"展开 / 收起"之间连续过渡：高度 + 不透明度一起走。

    直接 ``show()`` / ``hide()`` 是一帧跳变 —— 下面的内容会被瞬间顶下去或抽走，
    在高刷屏上看着很硬。这里把高度从 0 长到自然高度（反过来则压到 0 再隐藏），
    整段 120fps、曲线非线性，所以是一条连续的位移而不是一次跳变。
    """

    def __init__(self, widget: QWidget, duration: int = 200,
                 curve=in_out_cubic, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._widget = widget
        self._full_h = max(1, widget.sizeHint().height())
        self._t = 0.0
        self._effect: QGraphicsOpacityEffect | None = None
        self._tween = Tween(duration, curve, self._on_value, self._on_finished, self)

    @property
    def expanded(self) -> bool:
        return self._t >= 1.0

    @property
    def progress(self) -> float:
        return self._t

    def to(self, expanded: bool) -> None:
        if expanded:
            # 每次展开都重新量一次：换主题、换字号高度会变
            self._full_h = max(1, self._widget.sizeHint().height())
            self._widget.setVisible(True)
            self._ensure_effect()
        elif self._t <= 0.0 and not self._widget.isVisible():
            return                       # 已经收好了，别白跑一趟
        self._tween.start(self._t, 1.0 if expanded else 0.0)

    def _ensure_effect(self) -> None:
        if self._effect is None:
            self._effect = QGraphicsOpacityEffect(self._widget)
            self._widget.setGraphicsEffect(self._effect)

    def _on_value(self, value: float) -> None:
        self._t = value
        self._widget.setMaximumHeight(max(0, int(round(self._full_h * value))))
        if self._effect is not None:
            self._effect.setOpacity(value)

    def _on_finished(self) -> None:
        # 收尾要把透明度效果摘掉：留着会让这个控件常态走离屏合成
        self._widget.setGraphicsEffect(None)
        self._effect = None
        if self._t >= 1.0:
            # 展开到位就把高度上限放开，之后尺寸仍旧归布局管
            self._widget.setMaximumHeight(QWIDGETSIZE_MAX)
        else:
            self._widget.setVisible(False)
