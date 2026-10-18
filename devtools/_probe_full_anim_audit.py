# -*- coding: utf-8 -*-
r"""全量动画逐帧体检（10-18）——把软件里**每一个动画**都过一遍尺子。

与既有探针的分工
----------------
``_probe_anim_frames.py`` 只量"接收区/发送区/搜索条/主题"四条大动线；这里是
**全量枚举**：把软件里所有走 ``ui_anim`` 的补间、走 ``QTimer.singleShot`` 的手工
步进、以及"跟 hardcoded 几何变化同步发生"的动作都单独拎出来逐帧量一遍，按
team-lead 的三类问题（闪 / 不顺滑 / 逻辑不通顺）给出数字。

取样方式（必须）::

    ⛔ 取样挂在 ``ui_anim._Ticker._tick`` 上：每推进一拍补间才取一次样，采样与
    动画**同拍**。用 processEvents + sleep 轮询会因节拍错位把两拍并成一拍
    （假跳变），或漏拍（假平滑）。

判据（对每条动画的每个被驱动量）::

    P1 单帧大跳     某一步 ≥ 行程 15%（team-lead 判据①）
    P2 非单调       值的走向出现反向且回抽 > 行程 2%（扣完工整误差）
    P3 双落点       Δt < 4ms（同一帧两次赋值；按 memory 更准的写法是 Δq==0）
    P4 顿住         连续 ≥3 拍值不变、之后又继续走（前段/中段冻住）
    P5 起步即到     第一拍就把行程走完 ≥35%（动画事实上没跑）
    P6 末帧大跳     最后一拍 ≥ 行程 15%（落定被夹/补刀）
    P7 缺少补间     整个动画期间 ticker 一拍都没有（= 瞬切，闪的最大来源）

用法：python devtools/_probe_full_anim_audit.py
产物：_check/full_anim_audit.txt
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEV = os.path.dirname(os.path.abspath(__file__))
for p in (_ROOT, _DEV):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtWidgets import QApplication                     # noqa: E402

# ⛔ 桌面活性门禁：本机走 windows 平台插件会真的上屏；锁屏/休眠时一切"没变化"
# 的判据都会假 PASS。抓屏类判据必须先过这一关（黑白 ValueError + exit 2）。
from _desktop_gate import require_alive                        # noqa: E402


def _strip_check_path() -> None:
    """把 ``_check/`` 从 sys.path 里摘掉。

    ``_desktop_gate`` 为了复用 ``_check/desktop_alive.py`` 会把那个目录塞进
    sys.path —— 而 ``_check/`` 下也有一批**同名**模块副本（ui_controls.py 等），
    它们会抢在真正的源码前面被 import，于是 ``from ui_controls import FlashLabel``
    直接 ImportError（第一版探针就是这么挂的）。门禁已经调用过了，这里清掉。
    """
    for path in list(sys.path):
        if os.path.basename(path.rstrip("\\/")).lower() == "_check":
            sys.path.remove(path)
    for path in (_DEV, _ROOT):
        while path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)


_strip_check_path()

OUT_PATH = os.path.join(_ROOT, "_check", "full_anim_audit.txt")
LINES: list[str] = []


def say(line: str = "") -> None:
    print(line)
    LINES.append(line)


# --------------------------------------------------------------- 取样 + 分析

class Tap:
    """把取样挂到补间节拍上（每 tick 取一次）。"""

    def __init__(self, app):
        self.app = app
        self.reader = None
        self.rows: list[tuple[float, dict]] = []
        self._orig = None
        self._t0 = 0.0

    def __enter__(self):
        import ui_anim
        ticker = ui_anim._ticker()
        self._orig = ticker._tick

        def _patched():
            self._orig()
            if self.reader is not None:
                try:
                    self.rows.append((time.perf_counter() - self._t0,
                                      self.reader()))
                except Exception as exc:                    # noqa: BLE001
                    self.rows.append((time.perf_counter() - self._t0,
                                      {"__err__": repr(exc)}))
        ticker._tick = _patched
        return self

    def __exit__(self, *exc):
        import ui_anim
        ui_anim._ticker()._tick = self._orig
        return False

    def run(self, reader, action, settle_ms: float = 700.0):
        self.reader = reader
        self.rows = []
        action()
        self._t0 = time.perf_counter()
        # 上面 action 可能同步跑完第一拍（Tween.start 立刻送起点值），
        # 所以 t0 取 action 之后 = 动画真正开始推的时刻起算
        self._t0 = time.perf_counter()
        deadline = time.perf_counter() + settle_ms / 1000.0
        while time.perf_counter() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        return [(round(t * 1000.0, 2), v) for t, v in self.rows]


def _pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.003)


# 用例名 → (时长 ms, 曲线名)。曲线峰值由 curve_peak() 数值微分，不靠记忆。
#
# ⚠️ 本表与源码里的动画参数是**联动**的：改任何一处 Tween/Reveal 的时长或曲线，
#    必须同步改这里对应的条目，否则 analyze() 算"速度比"时分母用错曲线/时长
#    （报告数字会骗人 —— 阈值判据（P1~P7）不依赖本表，速度比依赖）。
#    先例：2026-10-18 搜索条 200ms in_out_quint → 220ms ease（S1 修复），
#    本表同轮更新；漏更新的那次速度比全部按旧曲线分母算过。
# ⚠️⚠️ 时长取**运行时** ``Tween.start(from, to, duration)`` 实参的那个值，
#    **不是构造器默认值**：按钮/卡片的悬停与按压都在 enter/leave/press 事件里
#    各自用不同 duration 重启同一根补间（ui_controls.py:227/231/235/239/380/384/
#    388/392、ui_surface.py:447/456），构造器写的 160/180 只是兜底默认。
#    先例：2026-10-18 审计按构造器默认值填过一轮分母（hover 150/卡片 180），
#    全部算错 —— 实测"末变时刻"（147/205/175/202ms）只认运行时的 140/200/170/200。
SPEC = {
    "入场淡入（intro）": (240, "standard"),              # 卡片 fade_in 默认 240
    "入场淡入（intro）·品牌页头": (220, "standard"),     # main_window.py:3137 显式 220
    "卡片悬停抬升": (170, "out_cubic"),                  # enter 170（ui_surface.py:447）
    "卡片悬停回落": (200, "out_cubic"),                  # leave 200（:456）
    "卡片描边闪": (380, "out_expo"),
    "图标按钮 hover": (140, "out_cubic"),                # enter 140（ui_controls.py:380）
    "图标按钮 hover 退出": (200, "out_cubic"),           # leave 200（:384）
    "图标按钮 press": (110, "out_cubic"),                # press 110（:388）
    "图标按钮 release": (160, "out_cubic"),              # release 160（:392）
    "文字按钮 hover": (150, "out_cubic"),                # InkButton enter 150（:227）
    "文字按钮 hover 退出": (220, "out_cubic"),           # leave 220（:231）
    "文字按钮 press": (110, "out_cubic"),                # press 110（:235）
    "文字按钮 release": (160, "out_cubic"),              # release 160（:239）
    "图标按钮 启用/禁用": (160, "out_cubic"),            # changeEvent（:413-415）
    "文字按钮 启用/禁用": (160, "out_cubic"),            # changeEvent（:259-261）
    "开关滑块": (190, "out_quint"),
    "下拉弹层展开": (150, "standard"),
    "数字/时钟高光衰减": (420, "out_expo"),
    "状态灯脉冲衰减": (560, "out_expo"),
    "状态灯忙碌旋转": (1000, "linear"),
    "搜索条展开": (220, "ease"),
    "搜索条收起": (220, "ease"),
    "发送区折叠": (300, "in_out_quint"),                 # start(duration=TX_ANIM_MS=300)
    "发送区展开": (300, "in_out_quint"),
    "接收区最大化": (260, "standard"),
    "退出最大化": (260, "standard"),
    "主题过渡": (280, "in_out_quint"),
    "主题过渡（切回）": (280, "in_out_quint"),
    "接收区平滑跟底": (180, "out_cubic"),
    "历史回放进入（提示条）": (220, "ease"),             # _REPLAY_BAR_MS=220
    "历史回放进入（交叉淡入）": (160, "standard"),       # _RX_XFADE_MS=160，曲线不同！
    "历史回放退出": (220, "ease"),
}


def curve_peak(curve) -> float:
    """曲线的**峰斜率**（归一化进度上的最大速度），数值微分。"""
    if curve is None:
        return 1.0
    n = 4000
    prev, best = curve(0.0), 0.0
    for i in range(1, n + 1):
        v = curve(i / n)
        best = max(best, (v - prev) * n)
        prev = v
    return max(best, 0.5)


def analyze(name: str, where: str, rows, duration_ms: float | None = None,
            curve=None, cyclic: bool = False, note: str = "",
            key_filter: tuple[str, ...] | None = None) -> None:
    """判一条动画。``duration_ms``/``curve_peak`` 给了才算"速度比"。

    ``key_filter``：只判这些 key（其余 key 连读都不读）。给"一次 tap.run 同时
    驱动着两条不同分母的动画"用（intro 的卡片 240/standard + 品牌页头 220/
    standard；回放入口的提示条 220/ease + 交叉淡入 160/standard）—— 混在一行里
    算速度比，分母必错其一。默认 None = 全都判（旧行为）。

    速度比（memory 的 G′ 口径）::
        ratio = |Δ值| ÷ (curve_peak × 行程 × Δt / 时长)
    ≈1.0 = 忠实跟随曲线；>1.25 说明这一拍多走了（掉拍合并 / 二次赋值 / 夹取）。
    Δt 取**相邻两次"真的变了"之间**的间隔，不是相邻两拍的间隔 —— 补间被
    ``min_interval_ms`` 节流时，中间那些"没变"的拍不属于这一跳。
    """
    say("-" * 78)
    say(f"【{name}】  {where}" + (f"   {note}" if note else ""))
    if duration_ms is None and name in SPEC:
        duration_ms, curve_name = SPEC[name]
        import ui_anim
        curve = curve or ui_anim.EASINGS.get(curve_name)
    if not rows:
        say("  ⚠️ 整个动画期间 ticker 一拍都没跑 → **没有任何补间**（= 瞬切，P7）")
        return
    times = [t for t, _ in rows]
    keys = [k for k in rows[0][1] if not k.startswith("__")]
    if key_filter is not None:
        wanted = set(key_filter)
        keys = [k for k in keys if k in wanted]
    for key in keys:
        vals = [v.get(key) for _, v in rows]
        if any(v is None for v in vals):
            continue
        vals = [float(v) for v in vals]
        # 裁掉尾部静置（落定之后 ticker 还在转的那一大段不算动画）
        last = len(vals) - 1
        while last > 0 and vals[last] == vals[last - 1]:
            last -= 1
        vals, times_k = vals[:last + 1], times[:last + 1]
        travel = max(vals) - min(vals)
        if travel <= 1e-6:
            say(f"    {key}: 全程未变（值恒 {vals[0]}）")
            continue
        # 只留"真的动了"的采样点（event），Δt 按 event 之间算
        events = [(times_k[0], vals[0])]
        for t, v in zip(times_k[1:], vals[1:]):
            if v != events[-1][1]:
                events.append((t, v))
        if len(events) < 2:
            say(f"    {key}: 只变了一次（{vals[0]}→{vals[-1]}，无过程）")
            continue
        steps = [(events[i + 1][0] - events[i][0],
                  events[i + 1][1] - events[i][1])
                 for i in range(len(events) - 1)]
        abs_steps = [abs(d) for _dt, d in steps]
        biggest = max(abs_steps)
        flags = []
        if biggest >= max(0.15 * travel, 1.0):
            idx = abs_steps.index(biggest)
            flags.append(f"P1 单帧大跳 {steps[idx][1]:+.0f}"
                         f"({biggest / travel * 100:.0f}%行程)"
                         f"@第{idx + 1}变/{steps[idx][0]:.1f}ms")
        if abs(steps[0][1]) >= max(0.35 * travel, 1.0):
            flags.append(f"P5 起步即到 {steps[0][1]:+.0f}")
        if abs(steps[-1][1]) >= max(0.15 * travel, 1.0):
            flags.append(f"P6 末帧大跳 {steps[-1][1]:+.0f}")
        if not cyclic:
            direction = 1 if vals[-1] > vals[0] else -1
            back = sum(abs(d) for _dt, d in steps if d * direction < 0)
            worst = max((abs(d) for _dt, d in steps if d * direction < 0),
                        default=0.0)
            if worst > max(0.02 * travel, 0.6):
                flags.append(f"P2 非单调 回抽合计 {back:.0f}/最大 {worst:.0f}")
        slow_gaps = [dt for dt, _d in steps if dt > 25.0]
        if slow_gaps:
            flags.append(f"P3′ 慢帧 {len(slow_gaps)} 处，最长 "
                         f"{max(slow_gaps):.0f}ms")
        peak_ = curve_peak(curve)
        if duration_ms and curve is not None:
            ratios = []
            for dt, d in steps:
                expect = peak_ * travel * max(dt, 0.5) / duration_ms
                if expect > 1e-6:
                    ratios.append((abs(d) / expect, dt, d))
            if ratios:
                top = max(ratios)
                mean = sum(r for r, _dt, _d in ratios) / len(ratios)
                flags.append(f"速度比 max {top[0]:.2f}× / 均值 {mean:.2f}×"
                             f"（峰 {top[1]:+.0f} 在 {top[1]:.1f}ms 内走 "
                             f"{top[2]:+.0f}）")
        show = " ".join(f"{v:.0f}" if abs(v) >= 1 or v == 0 else f"{v:.2f}"
                        for v in vals[:28])
        say(f"    {key}: 行程 {travel:.0f}  有效步 {len(events) - 1}  "
            f"首变 {events[0][0]:.0f}ms  末变 {events[-1][0]:.0f}ms")
        say(f"      序列（前 28）: {show}")
        say(f"      判据: {'； '.join(flags) if flags else 'OK'}")


def geo_reader(widget_getters, window, extra=None):
    """通用几何读数：屏幕坐标 x/y + 宽高 + 可见性。"""
    from PySide6.QtCore import QPoint

    def _read():
        out = {}
        for short, getter in widget_getters:
            w = getter(window)
            if w is None:
                continue
            try:
                p = w.mapToGlobal(QPoint(0, 0))
                g = w.geometry()
                out[f"{short}.x"] = p.x()
                out[f"{short}.y"] = p.y()
                out[f"{short}.w"] = g.width()
                out[f"{short}.h"] = g.height()
                out[f"{short}.vis"] = 1 if w.isVisible() else 0
            except RuntimeError:
                pass
        if extra:
            out.update(extra(window))
        return out
    return _read


def attr_reader(getters):
    """通用属性读数（透明度/进度/颜色等标量）。"""

    def _read():
        out = {}
        for short, getter in getters:
            try:
                out[short] = float(getter())
            except Exception:                                # noqa: BLE001
                out[short] = 0.0
        return out
    return _read


# ------------------------------------------------------------------- 主流程

def main() -> int:
    require_alive("full_anim_audit")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # ⛔ 探针还原口径：先存全量 QSettings 快照，finally 里写回并删掉探针新建的键
    # （主题切换那条用例会写 custom/theme_mode，不还原就是污染用户配置）
    from PySide6.QtCore import QSettings
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    from ui_theme import apply_scheme, ui_font
    apply_scheme("dark")
    app.setFont(ui_font(12.5))

    from ui_anim import display_max_hz, frame_hz, set_animations_enabled_override
    from ui_anim import set_frame_hz
    import ui_anim                     # 显式分母（curve=EASINGS[...]）共用这一次
    set_animations_enabled_override(True)
    set_frame_hz(display_max_hz())
    say("=" * 78)
    say(f"全量动画逐帧体检   取样节拍 = {frame_hz()}Hz（显示器上限 "
        f"{display_max_hz()}Hz）")
    say("取样挂在 ui_anim._Ticker._tick 上：每推一拍补间取一次样")
    say("=" * 78)

    _strip_check_path()      # 门禁可能又把 _check 塞回来了
    from PySide6.QtCore import QEvent
    from main_window import MainWindow

    window = MainWindow()
    window._port_watch.stop()
    window.show()
    window.apply_startup_state()
    # 入场淡入的抓拍起点：**不能**用 _pump(app, 0.15) 之类的一睡到底 ——
    # _play_intro 是构造后 50ms 的 singleShot（main_window.py:698），一睡 150ms
    # 起跑时品牌页头（220ms）只剩尾巴、卡片也已走过小半（实测 brand 只采到
    # 0.87→1 六步）。fade_in 是**同步挂效果（0.0）+ 延迟起补间**
    # （ui_surface.py:328-355），所以轮询等效果挂上即可：命中的那一刻所有目标
    # （brand + 3 卡）效果都已就位、补间未起，从 0.0 全程可采；None 回退分支
    # （读 1.0）不会触发，也就不会伪造"1.0→0.0"的假 P2。
    for _ in range(250):
        if window.brand_header.graphicsEffect() is not None:
            break
        app.processEvents()
        time.sleep(0.002)

    try:
        with Tap(app) as tap:
            # ---- ① 入场淡入（fade_in / QGraphicsOpacityEffect + card intro）----
            cards = window._cards
            geff_readers = []
            for i, card in enumerate(cards[:3]):
                geff_readers.append(
                    (f"card{i}_alpha",
                     (lambda c: (lambda: float(c.graphicsEffect().opacity())
                                 if c.graphicsEffect() is not None else 1.0
                                 ))(card)))
            geff_readers.append(
                ("brand_alpha",
                 lambda: float(window.brand_header.graphicsEffect().opacity())
                 if window.brand_header.graphicsEffect() is not None else 1.0))
            rows = tap.run(attr_reader(geff_readers), lambda: None, 900.0)
            # ⚠️ 卡片与品牌页头是**两条不同分母**的淡入（卡片走 fade_in 默认
            # 240ms；brand 显式 220ms，main_window.py:3137），必须拆两行判，
            # 混在一行里速度比的分母必错其一。
            analyze("入场淡入（intro）",
                    "ui_surface.py:317 fade_in（默认 240ms）/ "
                    "main_window.py:3135 _play_intro",
                    rows, key_filter=("card0_alpha", "card1_alpha", "card2_alpha"),
                    note="卡片走 fade_in 默认 240ms；"
                         "用 QGraphicsOpacityEffect（铁律例外项）")
            analyze("入场淡入（intro）·品牌页头",
                    "main_window.py:3137 fade_in(brand_header, duration_ms=220)",
                    rows, duration_ms=220, curve=ui_anim.EASINGS["standard"],
                    key_filter=("brand_alpha",))

            _pump(app, 0.5)

            # ---- ② 卡片悬停抬升 ----
            card = window._rx_card
            card._lift = 0.0        # 鼠标可能正好压在卡上（已 hover 满），先归零
            analyze("卡片悬停抬升",
                    "ui_surface.py:381 Tween(180, out_cubic, _on_lift)；"
                    "enter/leave 实际 170/200ms（:447/:456）",
                    tap.run(attr_reader([("lift", lambda: card._lift)]),
                            lambda: app.sendEvent(card,
                                                  QEvent(QEvent.Type.Enter)),
                            600.0))
            analyze("卡片悬停回落", "同上（leave 反向）",
                    tap.run(attr_reader([("lift", lambda: card._lift)]),
                            lambda: app.sendEvent(card,
                                                  QEvent(QEvent.Type.Leave)),
                            600.0))

            # ---- ③ 卡片描边闪 ----
            analyze("卡片描边闪",
                    "ui_surface.py:379 Tween(380, out_expo, _on_flash)",
                    tap.run(attr_reader([("flash", lambda: card._flash)]),
                            lambda: card.flash(), 700.0))

            # ---- ④ 图标按钮 hover / press ----
            # ⚠️ 不能用界面上真实的按钮：press 测试要发**真的鼠标按下事件**，
            #    打在 btn_expand 上就等于点了一下"最大化"（第一版探针就是这么
            #    把后续所有测量都带偏的 —— 发送区/接收区全在最大化态下量的）。
            #    这里造一颗**离屏**的同款按钮，事件打在它身上，界面零副作用。
            from ui_controls import IconButton
            btn = IconButton("\uE7C3", "探针用按钮", size=30, glyph_size=15)
            btn.resize(30, 30)
            btn.show()
            analyze("图标按钮 hover",
                    "ui_controls.py:352 Tween(150, out_cubic, _on_hover)；"
                    "enter/leave 实际 140/200ms（:380/:384）",
                    tap.run(attr_reader([("hover", lambda: btn._hover)]),
                            lambda: app.sendEvent(btn, QEvent(QEvent.Type.Enter)),
                            500.0))
            analyze("图标按钮 hover 退出", "同上（leave 反向）",
                    tap.run(attr_reader([("hover", lambda: btn._hover)]),
                            lambda: app.sendEvent(btn, QEvent(QEvent.Type.Leave)),
                            500.0))
            from PySide6.QtCore import QPointF, Qt as _Qt
            from PySide6.QtGui import QMouseEvent

            def _press():
                ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(6, 6),
                                 _Qt.MouseButton.LeftButton,
                                 _Qt.MouseButton.LeftButton,
                                 _Qt.KeyboardModifier.NoModifier)
                app.sendEvent(btn, ev)

            def _release():
                ev = QMouseEvent(QMouseEvent.Type.MouseButtonRelease,
                                 QPointF(6, 6), _Qt.MouseButton.LeftButton,
                                 _Qt.MouseButton.NoButton,
                                 _Qt.KeyboardModifier.NoModifier)
                app.sendEvent(btn, ev)

            analyze("图标按钮 press",
                    "ui_controls.py:354 Tween(120, out_cubic, _on_press)；"
                    "press/release 实际 110/160ms（:388/:392）",
                    tap.run(attr_reader([("press", lambda: btn._press)]),
                            _press, 400.0))
            analyze("图标按钮 release", "同上（松开反向）",
                    tap.run(attr_reader([("press", lambda: btn._press)]),
                            _release, 400.0))
            _pump(app, 0.3)

            # ---- ④b 文字按钮（InkButton）+ 两类按钮的启用/禁用过渡 ----
            # ⚠️ 同样是离屏按钮：InkButton 的 mouseReleaseEvent 会走
            #    QPushButton 默认逻辑（松在按钮区内就 emit clicked()），打在
            #    界面上等于点了"确定/取消/检查更新"（设置面板会开关一次）。
            from ui_controls import InkButton
            ink = InkButton("探针", variant="ghost")
            ink.show()
            analyze("文字按钮 hover",
                    "ui_controls.py:208 Tween(160, out_cubic, _on_hover)；"
                    "enter/leave 实际 150/220ms（:227/:231）",
                    tap.run(attr_reader([("hover", lambda: ink._hover)]),
                            lambda: app.sendEvent(ink, QEvent(QEvent.Type.Enter)),
                            500.0))
            analyze("文字按钮 hover 退出", "同上（leave 反向）",
                    tap.run(attr_reader([("hover", lambda: ink._hover)]),
                            lambda: app.sendEvent(ink, QEvent(QEvent.Type.Leave)),
                            500.0))

            def _ink_press():
                ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(6, 6),
                                 _Qt.MouseButton.LeftButton,
                                 _Qt.MouseButton.LeftButton,
                                 _Qt.KeyboardModifier.NoModifier)
                app.sendEvent(ink, ev)

            def _ink_release():
                ev = QMouseEvent(QMouseEvent.Type.MouseButtonRelease,
                                 QPointF(6, 6), _Qt.MouseButton.LeftButton,
                                 _Qt.MouseButton.NoButton,
                                 _Qt.KeyboardModifier.NoModifier)
                app.sendEvent(ink, ev)

            analyze("文字按钮 press",
                    "ui_controls.py:211 Tween(120, out_cubic, _on_press)；"
                    "press/release 实际 110/160ms（:235/:239）",
                    tap.run(attr_reader([("press", lambda: ink._press)]),
                            _ink_press, 400.0))
            analyze("文字按钮 release", "同上（松开反向）",
                    tap.run(attr_reader([("press", lambda: ink._press)]),
                            _ink_release, 400.0))

            # 启用/禁用过渡：changeEvent(EnabledChange) → _enabled_anim.start(
            # _enabled_t, 1/0, 160)（IconButton :413-415 / InkButton :259-261）。
            # ⚠️ 放在 hover/press **之后**测：禁用态的控件不再接收鼠标事件，
            #    先禁再用合成 Enter 就量不到了。两个方向分开跑（1→0、0→1）。
            analyze("图标按钮 启用/禁用",
                    "ui_controls.py:356 Tween(160, out_cubic, _on_enabled)；"
                    "changeEvent(EnabledChange) 驱动（:413-415）",
                    tap.run(attr_reader([("enabled", lambda: btn._enabled_t)]),
                            lambda: btn.setEnabled(not btn.isEnabled()), 500.0))
            analyze("图标按钮 启用/禁用", "同上（启用 0→1 反向）",
                    tap.run(attr_reader([("enabled", lambda: btn._enabled_t)]),
                            lambda: btn.setEnabled(not btn.isEnabled()), 500.0))
            analyze("文字按钮 启用/禁用",
                    "ui_controls.py:213 Tween(160, out_cubic, _on_enabled)；"
                    "changeEvent(EnabledChange) 驱动（:259-261）",
                    tap.run(attr_reader([("enabled", lambda: ink._enabled_t)]),
                            lambda: ink.setEnabled(not ink.isEnabled()), 500.0))
            analyze("文字按钮 启用/禁用", "同上（启用 0→1 反向）",
                    tap.run(attr_reader([("enabled", lambda: ink._enabled_t)]),
                            lambda: ink.setEnabled(not ink.isEnabled()), 500.0))
            _pump(app, 0.3)

            # ---- ⑤ 开关滑块 ----
            sw = window.chk_timestamp
            analyze("开关滑块",
                    "ui_controls.py:561 Tween(190, out_quint, _on_value)",
                    tap.run(attr_reader([("knob_t", lambda: sw._t)]),
                            lambda: sw.click(), 500.0))
            _pump(app, 0.3)

            # ---- ⑥ 下拉弹层 ----
            combo = window.cmb_baud
            analyze("下拉弹层展开",
                    "ui_controls.py:1176 Tween(150, STANDARD) + setWindowOpacity",
                    tap.run(
                        attr_reader([
                            ("h", lambda: float(
                                combo.view().window().geometry().height())),
                            ("opacity", lambda: float(
                                combo.view().window().windowOpacity())),
                        ]),
                        combo.showPopup, 500.0))
            combo.hidePopup()
            _pump(app, 0.3)

            # ---- ⑦ FlashLabel 高光衰减 ----
            analyze("数字/时钟高光衰减",
                    "ui_controls.py:1351 Tween(420, out_expo, _on_flash)",
                    tap.run(attr_reader([("flash",
                                          lambda: window.lbl_count._flash)]),
                            lambda: window.lbl_count.flash(), 700.0))

            # ---- ⑧ 状态灯脉冲 ----
            analyze("状态灯脉冲衰减",
                    "ui_controls.py:1414 Tween(560, out_expo, _on_value)",
                    tap.run(attr_reader([("level", lambda: window.pulse._level)]),
                            lambda: window.pulse.pulse(), 900.0))

            # ---- ⑨ 状态灯忙碌弧旋转（1000ms 无限循环）----
            analyze("状态灯忙碌旋转",
                    "ui_controls.py:1417 Tween(1000, linear, repeat=-1)",
                    tap.run(attr_reader([("spin_t",
                                          lambda: window.pulse._spin_t)]),
                            lambda: window.pulse.set_busy(True), 1200.0))
            window.pulse.set_busy(False)
            _pump(app, 0.3)

            # ---- ⑩ 搜索条 Reveal ----
            search = window._search_bar
            analyze("搜索条展开",
                    "main_window.py:1404 Reveal(_search_bar, 220, EASE)；"
                    "ui_anim.py:576 Reveal._on_value",
                    tap.run(
                        geo_reader([("bar", lambda w: search)], window),
                        lambda: window.btn_search.click(), 700.0))
            analyze("搜索条收起", "同上（反向）",
                    tap.run(
                        geo_reader([("bar", lambda w: search)], window),
                        lambda: window.btn_search.click(), 700.0))

            # ---- ⑪ 发送区折叠 / 展开（分割器补间）----
            # ⚠️ 先钉到"展开态"再量折叠，否则开机默认是折叠的、量到的是展开
            window._set_send_collapsed(False, remember=False)
            _pump(app, 0.8)

            def split_reader(_w):
                return {"split_tx": float(window._splitter.sizes()[1]),
                        "tx_h": float(window._tx_card.height())}

            analyze("发送区折叠",
                    "main_window.py:2975 Tween(240, in_out_quint, _on_split_anim)"
                    "（构造默认 240，实际 start(duration=TX_ANIM_MS=300)"
                    " @main_window.py:100）",
                    tap.run(
                        geo_reader([("tx", lambda w: w._tx_card),
                                    ("rx", lambda w: w._rx_card)], window,
                                   split_reader),
                        lambda: window.btn_minimize.click(), 900.0))
            analyze("发送区展开", "同上（反向）",
                    tap.run(
                        geo_reader([("tx", lambda w: w._tx_card),
                                    ("rx", lambda w: w._rx_card)], window,
                                   split_reader),
                        lambda: window.btn_minimize.click(), 900.0))

            # ---- ⑫ 接收区转场（一条 Tween(260, STANDARD)）----
            def rx_reader(_w):
                return {"rx_t": float(getattr(window, "_rx_t", 0.0))}

            analyze("接收区最大化",
                    "main_window.py:2344 Tween(_RX_TRANSITION_MS=260, STANDARD, "
                    "_on_rx_transition)",
                    tap.run(
                        geo_reader([("brand", lambda w: w.brand_header),
                                    ("config", lambda w: w._config_card),
                                    ("tx", lambda w: w._tx_card),
                                    ("rx", lambda w: w._rx_card),
                                    ("row", lambda w: w._open_row)], window,
                                   rx_reader),
                        lambda: window.btn_expand.click(), 1100.0))
            analyze("退出最大化",
                    "main_window.py:2344 同一条补间反向（Tween 可打断）",
                    tap.run(
                        geo_reader([("brand", lambda w: w.brand_header),
                                    ("config", lambda w: w._config_card),
                                    ("tx", lambda w: w._tx_card),
                                    ("rx", lambda w: w._rx_card),
                                    ("row", lambda w: w._open_row)], window,
                                   rx_reader),
                        lambda: window.btn_expand.click(), 1100.0))

            # ---- ⑬ 主题过渡 ----
            from ui_theme import qcolor

            def theme_r():
                return float(qcolor("surface").red())

            def theme_g():
                return float(qcolor("surface").green())

            analyze("主题过渡",
                    "main_window.py:729 Tween(280, in_out_quint, _on_theme_mix)",
                    tap.run(attr_reader([("surface_R", theme_r),
                                         ("surface_G", theme_g)]),
                            lambda: window._animate_theme("light"), 800.0))
            _pump(app, 0.4)
            analyze("主题过渡（切回）", "同上",
                    tap.run(attr_reader([("surface_R", theme_r),
                                         ("surface_G", theme_g)]),
                            lambda: window._animate_theme("dark"), 800.0))

            # ---- ⑭ 接收区平滑跟底（滚动补间）----
            # ⚠️ 空文档没有可滚距离（maximum=0 → 补间不起跑），先灌一批行
            window.txt_rx.setPlainText("\n".join(f"行{i}" for i in range(400)))
            app.processEvents()
            bar = window.txt_rx.verticalScrollBar()
            say(f"  （滚动量程 maximum={bar.maximum()}）")
            analyze("接收区平滑跟底",
                    "main_window.py:3806 Tween(180, out_cubic, _on_rx_scroll)",
                    tap.run(
                        attr_reader([("scroll", lambda: float(bar.value()))]),
                        lambda: (bar.setValue(0),
                                 window._follow_rx_bottom()), 700.0))

            # ---- ⑮ 回放入口：提示条高度补间 + 交叉淡入 ----
            try:
                rows = tap.run(
                    geo_reader([("bar", lambda w: w._replay_bar)], window,
                               lambda w: {
                                   "xfade": float(w._rx_shot.alpha())
                                   if getattr(w, "_rx_shot", None) is not None
                                   else 0.0}),
                    lambda: window.btn_open_history.click(), 1400.0)
                # ⚠️ 一次 tap.run 同时驱动两条**不同分母**的动画：提示条高度
                # （220ms EASE）与交叉淡入（160ms STANDARD）。必须拆两行判，
                # 旧版混一行给 (220, ease) 分母，xfade 的速度比全是错算的。
                analyze("历史回放进入（提示条）",
                        "main_window.py:4350 Tween(_REPLAY_BAR_MS=220, EASE)",
                        rows)
                analyze("历史回放进入（交叉淡入）",
                        "main_window.py:4463 Tween(_RX_XFADE_MS=160, STANDARD)",
                        rows, duration_ms=160, curve=ui_anim.EASINGS["standard"],
                        key_filter=("xfade",))
                _pump(app, 0.4)
                rows = tap.run(
                    geo_reader([("bar", lambda w: w._replay_bar)], window),
                    lambda: window.btn_open_history.click(), 1400.0)
                analyze("历史回放退出", "同上（反向）", rows)
            except Exception as exc:                        # noqa: BLE001
                say(f"\n【历史回放】跳过：{exc!r}")
            _pump(app, 0.5)

        # ---- ⑯ 设置面板：入场几何补间 + 换帧渐隐（现走 Tween）----
        # 渐隐已从"6 次 QTimer.singleShot(16*i) 的手工步进"改成
        # Tween(_SETTINGS_FADE_MS=96, in_out_cubic)（main_window.py:5009，
        # 共享 165Hz 节拍推，96ms ≈ 12 级）。2ms 轮询的抓法对两种驱动都成立
        # （记录每一次 alpha 变化的时刻，看台阶是否均匀），保留不动。
        say("-" * 78)
        say("【设置面板：入场 + 换帧渐隐】  main_window.py:4748 "
            "Tween(260, STANDARD)；:4984 _start_settings_swap_fade → "
            "Tween(_SETTINGS_FADE_MS=96, in_out_cubic) @:5009")
        samples: list[tuple[float, float]] = []

        def poll():
            t0 = time.perf_counter()
            end = t0 + 2.2
            while time.perf_counter() < end:
                app.processEvents()
                shot = window._settings_shot
                if shot is not None:
                    try:
                        # _SettingsShot 没有 alpha() 取值器（只有 _RxShot 有）
                        val = float(shot._alpha)
                    except (RuntimeError, AttributeError):
                        val = -1.0
                    now = time.perf_counter() - t0
                    if not samples or abs(samples[-1][1] - val) > 1e-6:
                        samples.append((now * 1000.0, val))
                time.sleep(0.002)

        try:
            window.btn_settings.click()
            poll()
        except Exception as exc:                            # noqa: BLE001
            say(f"  ⚠️ 设置面板探针失败：{exc!r}")
        if samples:
            levels = [round(v, 3) for _t, v in samples]
            say(f"  快照 alpha 台阶：{[round(v, 3) for _t, v in samples]}")
            say("  （每一级的到达时刻 ms）: " +
                " ".join(f"{v:.3f}@{t:.0f}" for t, v in samples))
            dts = [b[0] - a[0] for a, b in zip(samples, samples[1:])]
            if dts:
                say(f"  台阶间隔：{['%.0f' % d for d in dts]}ms "
                    f"（165Hz 共享节拍期望 ≈6ms/级；最大偏差 "
                    f"{max(abs(d - 6.0) for d in dts):.0f}ms）")
            biggest = max((abs(samples[i + 1][1] - samples[i][1])
                           for i in range(len(samples) - 1)), default=0.0)
            say(f"  单步最大 alpha 跳变 {biggest:.3f}"
                f"（行程 1.0 → {biggest * 100:.0f}%；96ms/165Hz ≈ 12 级，"
                f"理论每级 8.3%）")
            if levels and min(levels) > 0.0:
                say(f"  ⚠️ 收尾时 alpha 未归零就撤快照（最低 {min(levels)}）"
                    "—— 那一帧是硬切")
        else:
            say("  ⚠️ 没有采到快照 alpha 变化")

        # 收场
        try:
            window._close_settings_dialog(False)
            _pump(app, 0.6)
        except Exception:                                    # noqa: BLE001
            pass
    finally:
        try:
            window.setGeometry(0, 0, 1134, 820)
            window.close()
        except RuntimeError:
            pass
        text = "\n".join(LINES)
        with open(OUT_PATH, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        say(f"\n逐帧数据 → {OUT_PATH}")
        for key in list(settings.allKeys()):
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            if value is not None:
                settings.setValue(key, value)
        settings.sync()
        say("QSettings 已还原（并清掉探针新建的键）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
