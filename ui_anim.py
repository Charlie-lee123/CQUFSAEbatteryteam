"""统一动画引擎：帧率可调（默认 120fps，上限跟显示器刷新率）、一律非线性缓动、
所有补间共用一条计时器。

为什么自己写这一层，而不是继续用 ``QVariantAnimation`` / ``QPropertyAnimation``：
Qt 的动画节拍由它内部的统一计时器决定，不由我们定（Qt6 默认约 60fps）。在
120Hz 屏幕上，60fps 的过渡一眼能看出"两帧才动一次"的台阶。而这个界面里的动效
全是几十毫秒的短过渡，帧率不够就显脏 —— 所以节拍必须自己拿住。

- **节拍**：默认 120fps（``frame_interval_ms()`` = 8ms），可在设置里无级调到
  显示器刷新率上限。用 ``PreciseTimer``，普通精度的计时器会被系统拉到 15ms 左右，
  那就掉回 60fps 了。**改帧率不改时长**（进度按真实时间算，见上）。
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

# ---------------------------------------------------------------- 帧率
#
# 帧率**可在设置里无级调节**（2026-10-17 用户点名：上限跟着显示器刷新率走，
# 他那台笔记本是 165Hz）。默认 120 —— "高刷屏上看不出台阶"的门槛。
#
# ⛔ 帧率只改"每帧隔多久推一次"，**绝不改动画时长**：``Tween.advance()`` 的进度
#    按 ``perf_counter`` 算，不数回调次数。所以 60Hz 和 165Hz 播同一段 240ms 动画，
#    墙钟时长完全一致，只是采样密度不同 —— 这就是用户要的"速度同步"。
#    改节拍时正在跑的补间也不会跳：下一帧按真实流逝时间重新算进度。
FPS_DEFAULT = 120
FPS_MIN = 24              # 再低就明显一卡一卡了
FPS_MAX_CAP = 240         # 防呆上限；真实上限还会被显示器刷新率再压一次

_frame_hz = FPS_DEFAULT
_screen_hz_cache: int | None = None


def display_max_hz() -> int:
    """主屏的最高刷新率（Hz）。取不到（远程桌面 / 虚拟屏）就退回 120。

    这是设置面板滑杆的右端，也是 ``set_frame_hz`` 的硬上限 —— 超过显示器
    刷新率没有任何意义（只会白烧 CPU）。
    """
    global _screen_hz_cache
    if _screen_hz_cache is not None:
        return _screen_hz_cache
    hz = 0.0
    try:
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        if screen is not None:
            hz = float(screen.refreshRate())
    except (AttributeError, RuntimeError, TypeError):
        hz = 0.0
    if hz < 20.0:                       # 0 / 异常值都不可信
        hz = float(FPS_DEFAULT)
    _screen_hz_cache = int(min(FPS_MAX_CAP, round(hz)))
    return _screen_hz_cache


def forget_screen_hz() -> None:
    """丢掉显示器刷新率缓存（换屏 / 分辨率变更后调用）。"""
    global _screen_hz_cache
    _screen_hz_cache = None


def frame_hz() -> int:
    """当前生效的动画帧率。"""
    return _frame_hz


def set_frame_hz(hz: int | float) -> int:
    """设置动画帧率，返回**实际生效值**（已夹到 [FPS_MIN, 显示器上限]）。"""
    global _frame_hz
    cap = min(FPS_MAX_CAP, display_max_hz())
    value = int(round(float(hz)))
    value = max(FPS_MIN, min(cap, value))
    _frame_hz = value
    if _ticker_instance is not None:
        _ticker_instance.set_frame_hz(value)
    return value


def frame_interval_ms() -> int:
    """节拍间隔（毫秒）—— ``round(1000/Hz)``，保证实际刷新率 ≥ 目标帧率。"""
    return max(1, round(1000.0 / _frame_hz))

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


def in_out_quint(t: float) -> float:
    """两头慢、中间快的加强版（GSAP ``power3.inOut`` 一档）。

    整块内容的展开/收起用它：起止都极轻，中段又比 in_out_cubic 更果断 ——
    "长距离位移"要的是这个手感，用 cubic 会显得拖。
    """
    if t < 0.5:
        return 16.0 * t ** 5
    return 1.0 - (-2.0 * t + 2.0) ** 5 / 2.0


def bezier(x1: float, y1: float, x2: float, y2: float,
           samples: int = 64) -> Callable[[float], float]:
    """CSS ``cubic-bezier()`` 曲线的求值器（等价 GSAP 的 CustomEase）。

    控制点给的是 (x, y) ∈ [0,1]²，但曲线参数 t 与"进度" x 不是同一个量 ——
    所以先按 x 反查 t（单调二分），再求 y。采样表建一次、之后是查表 + 线性插值，
    在 120Hz 的节拍里不会成为负担（每条曲线只建一次，缓存在闭包里）。
    """
    table: list[float] = []
    for i in range(samples + 1):
        t = i / samples
        inv = 1.0 - t
        # 三次贝塞尔，P0=(0,0)、P3=(1,1)
        bx = 3.0 * inv * inv * t * x1 + 3.0 * inv * t * t * x2 + t ** 3
        by = 3.0 * inv * inv * t * y1 + 3.0 * inv * t * t * y2 + t ** 3
        table.append((bx, by))

    def curve(progress: float) -> float:
        x = 0.0 if progress < 0.0 else (1.0 if progress > 1.0 else progress)
        lo, hi = 0, samples
        while lo < hi:                       # 表按 x 单调递增，二分定位
            mid = (lo + hi) // 2
            if table[mid][0] < x:
                lo = mid + 1
            else:
                hi = mid
        if lo == 0:
            return table[0][1]
        x0, y0 = table[lo - 1]
        x1_, y1_ = table[lo]
        span = x1_ - x0
        k = 0.0 if span <= 1e-12 else (x - x0) / span
        return y0 + (y1_ - y0) * k

    return curve


# 设计系统的"标准曲线"：起步略缓、收尾极稳（Material 的 standard easing）。
# 弹层展开、淡入这类"内容出现"的场合用它 —— out_* 系起步就是最大速度，
# 用在"出现"上会显得东西是被甩进来的，不是被放上去的。
STANDARD = bezier(0.2, 0.0, 0.0, 1.0)

# CSS 的 ``ease`` = ``cubic-bezier(0.25, 0.1, 0.25, 1)``：入段比 STANDARD 长一点
# （起步不至于"甩"），但比 in_out_cubic 短得多（不会"顿一下再开始"）。「两头都轻、
# 但每一帧都在动」这条需求下它是最平衡的一档。
#
# 什么时候用它：动的是**版式占位**（高度/宽度，会把别的内容顶走）而非透明度/缩放。
# 这类动画的验收口径是"看得见在动"—— 拿 220ms 展开 50px 那条提示条举例，同一个
# 补间引擎 + ``min_interval_ms=16`` + ``max(1, round(v))`` 量化地板，逐帧高度是：
#   STANDARD      : 1,10,23,31,36,40,43,45,47,48,49,50   首帧就窜 10px（太冲）
#   in_out_quint  : 1, 3, 8,19,34,44,48,49,50            前 72ms 全在 3px 以内
#   in_out_cubic  : 1, 3, 7,13,21,31,39,44,47,49,50      前 56ms 只到 3px（"顿一下"）
#   EASE          : 1, 5,13,21,28,34,39,42,45,47,48,49,50  13 个台阶、单步 ≤8px、
#                                                           首个 ≥2px 在 24ms
# 即：台阶最多、单步最小、起步既不像 out_* 那样"被甩进来"也不像 in_out_* 那样"先
# 停一拍"。用户对提示条的反馈就是"顿一下再开始"，量化到数字上就是 56ms 才动第二格。
#
# ⛔ 别在业务代码里直接 ``bezier(...)`` 造曲线：这里是**调参单点**，改一处全体生效。
EASE = bezier(0.25, 0.1, 0.25, 1.0)

EASINGS: dict[str, Callable[[float], float]] = {
    "linear": linear,
    "out_cubic": out_cubic,
    "out_quint": out_quint,
    "in_out_cubic": in_out_cubic,
    "in_out_quint": in_out_quint,
    "out_expo": out_expo,
    "standard": STANDARD,
    "ease": EASE,
}


def _resolve(curve) -> Callable[[float], float]:
    if callable(curve):
        return curve
    return EASINGS.get(str(curve), out_cubic)


# ---------------------------------------------------------------- 减弱动效


_animations_override: bool | None = None


def set_animations_enabled_override(value: bool | None) -> None:
    """强制开关（自测用）：None = 回到读系统设置。"""
    global _animations_override
    _animations_override = value


def animations_enabled() -> bool:
    """系统是否允许播放动画 —— Windows「辅助功能 → 视觉效果 → 动画效果」。

    这是 GSAP 技能里 ``gsap.matchMedia("(prefers-reduced-motion)")`` 那一条的
    桌面版：关掉时**不是"什么都不做"，而是把过渡压成 0 时长**，直接落到终态 ——
    界面对这类用户应该是"干脆地切换"，而不是"什么都没有发生"。

    SPI_GETCLIENTAREAANIMATION（0x1042）就是那个开关。取不到时按"开"处理：
    默认行为不变，不会因为一次 API 失败就把全局动效给关了。
    """
    if _animations_override is not None:
        return _animations_override
    try:
        import ctypes
        enabled = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            0x1042, 0, ctypes.byref(enabled), 0)
        if ok:
            return bool(enabled.value)
    except (AttributeError, OSError):
        pass
    return True


# ---------------------------------------------------------------- 补间

class Tween(QObject):
    """把一个值从 ``start`` 连续推到 ``end``，时长以毫秒计。

    调用方只关心"给我一串中间值"，不关心帧从哪来 —— 帧由共享计时器统一派发，
    所以一个界面上十个动画在跑也只有一条计时器。
    """

    def __init__(self, duration: int = 200, curve=out_cubic,
                 on_value: Callable[[float], None] | None = None,
                 on_finished: Callable[[], None] | None = None,
                 parent: QObject | None = None,
                 repeat: int = 0, yoyo: bool = False,
                 min_interval_ms: int = 0) -> None:
        super().__init__(parent)
        self.duration = max(1, int(duration))
        # 最低帧间隔（毫秒）：0 = 每帧都送（默认 125Hz）。给"每帧都要改布局、
        # 重绘大面积"的重动画用 —— 那种动画硬撑 125Hz 会让调度器掉帧，观感反而
        # 是"黏、不顺"（用户对接收区展开的原话）。16ms ≈ 60fps，工作量减半、
        # 节拍稳定，肉眼更顺。终点帧**不会被吞**（见 advance）。
        self._min_interval = max(0, int(min_interval_ms)) / 1000.0
        self._last_emit = 0.0
        self._curve = _resolve(curve)
        self._on_value = on_value
        self._on_finished = on_finished
        self._from = 0.0
        self._to = 1.0
        self._begin = 0.0
        self._last_emit = 0.0          # 新一段不受上一段的节流影响
        self._running = False
        # repeat/yoyo 取 GSAP 的语义：0=只跑一次，-1=无限循环，n=再跑 n 轮；
        # yoyo=true 时每轮反向（来回摆动）。给"一直转到状态结束"的指示器用。
        self._repeat = int(repeat)
        self._yoyo = bool(yoyo)
        self._cycle = 0
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
        self._cycle = 0
        # 系统关掉了动画（辅助功能）：不推帧，直接落终态 —— 该变的还是要变，
        # 只是不再"花时间变"。
        if not animations_enabled():
            self._running = False
            _ticker().discard(self)
            self._deliver(self._to)
            if self._on_finished is not None:
                self._on_finished()
            return self
        self._begin = time.perf_counter()
        self._running = True
        _ticker().add(self)
        self._deliver(self._from)        # 立刻补一次起点，避免停在旧值上等第一帧
        return self

    def stop(self, finished: bool = False) -> None:
        # ⚠️ ``parent.destroyed`` 是 ``destroyed(QObject*)``：Qt 会把**被销毁的
        #    控件**传进 ``finished``（不是 bool）。这里显式把"非 bool"当成 True ——
        #    宿主都没了，这段动画等同于"结束"，收尾回调该走一次
        #    （main_window._finish_rx_xfade / _apply_settings_result 依赖这个语义：
        #    拆窗口时补一次收尾，否则关闭途中改的设置不会被落盘）。
        if not isinstance(finished, bool):
            finished = True
        if not self._running:
            return
        self._running = False
        _ticker().discard(self)
        if finished and self._on_finished is not None:
            self._on_finished()

    def advance(self, now: float) -> bool:
        """推进一步。返回 False 表示这段已经跑完。"""
        frame = self.duration / 1000.0
        progress = (now - self._begin) / frame
        if (self._min_interval and self._last_emit
                and now - self._last_emit < self._min_interval
                and progress < 1.0):
            return True                      # 节流：这一拍先不送值（终点不拦）
        self._last_emit = now
        if progress >= 1.0:
            if self._repeat != 0 and (self._repeat < 0 or self._cycle < self._repeat):
                # 开下一轮。**记满一拍而不是从"现在"重新起算**：计时器晚一帧
                # 到达时，进度按实际时间接着走，循环处不会抖一下（连贯性）。
                self._cycle += 1
                self._begin += frame * int(progress)
                if self._yoyo and self._cycle % 2 == 1:
                    self._from, self._to = self._to, self._from
                self._deliver(self._from)
                progress = (now - self._begin) / frame
                if progress >= 1.0:               # 追平了，下一帧继续
                    return True
            else:
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
        self._timer.setInterval(frame_interval_ms())
        self._timer.timeout.connect(self._tick)
        self._pending: list[Tween] = []

    def set_frame_hz(self, hz: int) -> None:
        """改节拍。正在跑的话直接换间隔 —— 补间按真实时间算进度，不会跳。"""
        interval = max(1, round(1000.0 / max(1, int(hz))))
        if interval != self._timer.interval():
            self._timer.setInterval(interval)
            if self._timer.isActive():
                self._timer.start(interval)     # 立刻按新节拍重排

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
            # ⚠️ **一个补间出错不能拖死其它补间**：advance 会回调业务代码
            #    （改几何、碰控件），那里一旦抛异常，异常会从 for 里冒出去，
            #    排在后面的补间这一帧就全都不推进了 —— 表现是"界面卡住不动"，
            #    而且要等下一次 tick 再踩同一颗雷。这里逐个兜住：坏的立刻摘掉。
            try:
                tween.advance(now)
            except Exception:          # noqa: BLE001 - 隔离坏补间，见上
                import traceback
                traceback.print_exc()
                tween.stop()


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
    节拍不会，所以"够不够 120fps"要断言在这里。**它随设置的帧率变**。"""
    return _ticker()._timer.interval()


# ---------------------------------------------------------------- 展开/收起

class Reveal(QObject):
    """让控件在"展开 / 收起"之间连续过渡：高度 + 不透明度一起走。

    直接 ``show()`` / ``hide()`` 是一帧跳变 —— 下面的内容会被瞬间顶下去或抽走，
    在高刷屏上看着很硬。这里把高度从 0 长到自然高度（反过来则压到 0 再隐藏），
    整段 120fps、曲线非线性，所以是一条连续的位移而不是一次跳变。
    """

    def __init__(self, widget: QWidget, duration: int = 220,
                 curve=in_out_quint, parent: QObject | None = None,
                 horizontal: bool = False, min_interval_ms: int = 0) -> None:
        super().__init__(parent)
        self._widget = widget
        # 水平模式：动画走 maximumWidth（右侧栏"向右折叠"用），其余同垂直。
        self._horizontal = bool(horizontal)
        self._full_h = max(1, widget.sizeHint().height())
        self._full_w = max(1, widget.sizeHint().width())
        # 横向收缩期间暂存的最小宽度（见 to()）
        self._min_w: int | None = None
        # 当前这段是折叠（收）还是展开（放）—— "缩到 1/8 就隐藏"只对折叠生效，
        # 展开时第一帧 value 就是 0，无差别套用会把控件先藏一帧（搜索条就这么
        # 被卡住过：点开搜索 → 第一帧隐藏 → 用例看到的是没展开）。
        self._collapsing = False
        self._t = 0.0
        self._effect: QGraphicsOpacityEffect | None = None
        self._tween = Tween(duration, curve, self._on_value, self._on_finished,
                            self, min_interval_ms=min_interval_ms)
        # 控件先析构就把补间停掉：补间挂在 Reveal 上，控件那边的保险丝管不到它
        self._widget.destroyed.connect(self._on_widget_destroyed)

    @property
    def expanded(self) -> bool:
        return self._t >= 1.0

    @property
    def progress(self) -> float:
        return self._t

    def to(self, expanded: bool) -> None:
        # ⛔ 折叠时满量程要取**当前真实尺寸**，不能只看 sizeHint：BrandHeader 的
        #    sizeHint 是 350，布局实际只给它 164 —— 按 350 缩的话前 40% 的时间
        #    视觉上什么都没发生（maxHeight 还没压到实际高度以下），然后突然从
        #    164 开始掉，而且在 0.125×350 = 44px 就被 hide（一帧消失 44px）。
        #    取真实高度后：164→0 全程匀速可感，hide 阈值也落在 20px 上。
        if not expanded:
            actual = (self._widget.width() if self._horizontal
                      else self._widget.height())
            if actual > 0 and actual < (self._full_w if self._horizontal
                                        else self._full_h):
                if self._horizontal:
                    self._full_w = actual
                    # `_min_w` 是"展开时钉回去的原宽"，同样可能比现在的实际宽度大
                    # （复用的 Reveal 在卡片更宽时记过数）—— 不一起夹小，收尾钉回
                    # 去的那一下会多出一截。
                    if self._min_w is None or self._min_w > actual:
                        self._min_w = actual
                else:
                    self._full_h = actual
        if expanded:
            # 每次展开都重新量一次：换主题、换字号尺寸都会变
            self._full_h = max(1, self._widget.sizeHint().height())
            # ⚠️ 横向的"完整宽度"要取**控件当前实际宽度**，不能用 sizeHint：
            #    右侧栏那张卡是 setFixedWidth(310) 钉死的，但它的 sizeHint 有 800+
            #    （内容想要的自然宽度）。拿 sizeHint 当满量程，收缩比例就全错，
            #    而且结束时会把卡片钉成 sizeHint 那么宽（窗口最小宽度跟着变大，
            #    几何回不到原位 —— 自测里 839 被顶成 1134）。
            self._full_w = max(1, self._min_w or self._widget.width()
                               or self._widget.sizeHint().width())
            if self._horizontal and self._min_w is None:
                # ⚠️ 右侧栏那张卡是 `setFixedWidth(310)` 钉死的：固定宽度下改
                #    maximum 完全无效（实测宽度纹丝不动）。横向收缩一律走
                #    **setFixedWidth**（它同时改 min 和 max，对固定宽度控件生效），
                #    结束后再钉回原宽。
                self._min_w = self._widget.width() or self._full_w
            self._widget.setVisible(True)
            self._ensure_effect()
        elif self._t <= 0.0 and not self._widget.isVisible():
            return                       # 已经收好了，别白跑一趟
        if (self._horizontal and self._min_w is None and self._t >= 0.99):
            # 在**第一次折叠开始**时记录原始宽度（此刻它还是展开的）。
            # 只记不够 —— 之前记在"展开分支"里，而首次调用恰恰是折叠，
            # 于是永远记不上，收完就钉在窄宽度回不去（实测 110 卡住）。
            self._min_w = self._widget.width() or self._full_w
        self._collapsing = not expanded
        self._tween.start(self._t, 1.0 if expanded else 0.0)

    def _ensure_effect(self) -> None:
        """**故意什么都不做**（2026-10-17）。

        ⛔ 曾经给每个动画中的控件挂 ``QGraphicsOpacityEffect`` 并逐帧 setOpacity，
        但 Qt 里一旦挂上这个特效，该控件就**只能走离屏合成**（每帧先画到离屏缓冲
        再合成回来）。接收区最大化时**四个块同时在动画 + 全屏尺寸变化**，四层离屏
        合成 = 必然掉帧 —— 用户报的"最大化和退出的动画有抽搐"就是这个。
        尺寸动画本身已是连续位移，不需要再叠一层渐隐。
        """
        return

    def _on_widget_destroyed(self) -> None:
        """宿主控件先走一步：立刻停表。

        ⚠️ 本类的补间是挂在 **Reveal 自己** 身上的（不是挂在控件上），所以控件
        析构时那条 ``destroyed → stop`` 的保险丝**不会**替我们跳闸 —— 补间还在
        推帧，回调就会去碰已经析构的 C++ 对象（实测收尾时炸 RuntimeError：
        setGraphicsEffect 踩空）。窗口关得急、展开动画还在跑时必现，所以这里
        自己接一根线。
        """
        self._tween.stop()

    def _on_value(self, value: float) -> None:
        self._t = value
        try:
            # ⚠️ 缩到 1/8 就隐藏，**两个方向同一套规则**：
            #   ① 观感上已经是"收没了"，再走最后 1/8 没有意义；
            #   ② 避开"尺寸收到 0 时卡片阴影/磨砂建出无效 QImage 而崩"的边界
            #      （实测 constBits() 返回 None）；
            #   ③ 横向折叠的配置卡与竖向收掉的发送区会在**同一时刻**消失
            #      （同一条曲线、同一个阈值、同样的时长）—— 用户点名要同步。
            # ⛔ 阈值不能大：0.125 意味着 164px 的页头在**还剩 20px** 时被整块
            #    hide，布局随即把这一截一次性抽走 —— 逐帧实测 4 块同时隐藏那一帧
            #    整体位移 80~91px，用户看到的就是"结尾跳一下"。收到 0.04（页头约
            #    6px）再隐藏，这一帧只剩几像素的变化，肉眼看不出；也仍然避开
            #    "尺寸归 0 时磨砂/阴影建出无效 QImage"那条老坑。
            if self._collapsing and value <= 0.04:
                self._widget.setVisible(False)
                return
            if not self._widget.isVisible():
                self._widget.setVisible(True)     # 值回到阈值以上要显回来
            if self._horizontal:
                self._widget.setFixedWidth(
                    max(8, int(round(self._full_w * value))))
            else:
                self._widget.setMaximumHeight(max(0, int(round(self._full_h * value))))
            if self._effect is not None:
                self._effect.setOpacity(value)
        except RuntimeError:
            # 析构竞态：这一帧刚好落在控件被删之后（destroyed 信号是排队送达的，
            # 存在一帧窗口）。停表就够了，不必抛给上层。
            self._tween.stop()

    def _on_finished(self) -> None:
        # 收尾要把透明度效果摘掉：留着会让这个控件常态走离屏合成
        try:
            self._widget.setGraphicsEffect(None)
        except RuntimeError:
            self._tween.stop()
            return
        self._effect = None
        if self._t >= 1.0:
            # 展开到位就把上限放开，之后尺寸仍旧归布局管
            if self._horizontal:
                # ⚠️ 记下的"原宽"要**一直留着**：还原时若清空，下一次折叠又会在
                #    半宽状态下重新记录，宽度就一路缩水（实测 310→112 回不去）。
                if self._min_w is not None:
                    self._widget.setFixedWidth(self._min_w)
            else:
                self._widget.setMaximumHeight(QWIDGETSIZE_MAX)
        else:
            self._widget.setVisible(False)
