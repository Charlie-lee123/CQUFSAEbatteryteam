# -*- coding: utf-8 -*-
r"""退出段"第二次落地"定位：给控件装 eventFilter，把间隔 <8ms 的连续 Resize 连同
调用栈打出来 —— 到底是谁在补间那一拍之后又改了一次几何。

为什么需要它
------------
接收区转场是 165Hz 节拍 + ``min_interval_ms=16`` 送值：几何**本来**每 16~18ms 才变
一次。QA 却量到退出段有一处 ``100px / 6.1ms``（15.6px/ms，理论峰值 4.91 的 3.4 倍），
而且接收卡 / 日志区 / 功能列**同帧同幅**变化 —— 说明在补间送值之后**几毫秒**，还有
一次独立的布局动作把三块一起推了一截。这种"第二把刀"在只看几何序列的探针里只能
看出"某一拍走得特别远"，看不出是谁干的；只有抓调用栈能直接指认。

做法
----
1. 在 ``brand_header`` / ``_config_card`` / ``_rx_opts_row`` / ``txt_rx`` /
   ``_rx_rail`` / ``_rx_card`` / ``_tx_card`` / ``_splitter`` 上装 eventFilter，只收
   ``Resize`` / ``Move`` / ``LayoutRequest``，记录 ``(t, 控件, 新矩形, 调用栈 3 帧)``。
2. 跑一次**退出**，把"与上一次几何变化间隔 <8ms"的连续事件连栈打印。
3. 顺手做一条硬断言：``_on_rx_done`` 前后各记一次全部控件矩形，**diff 必须为 0**
   —— 落定路径在几何上应该是 no-op（末帧就是终态）。这条现在大概率不成立，
   差值就是"最后一跳"的幅度。

用法：python devtools/_probe_rx_finish_layout.py
     （可加 --dir expand 只跑展开方向做对照）
"""
from __future__ import annotations

import os
import sys
import time
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import QEvent, QObject, QPoint, QRect       # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget              # noqa: E402

GAP_MS = 8.0            # 认为"同一拍里的第二次落地"的时间窗
SETTLE_MS = 620.0
TRACK = (
    ("rx_card", "_rx_card"),
    ("txt_rx", "txt_rx"),
    ("rail", "_rx_rail"),
    ("config", "_config_card"),
    ("opts", "_rx_opts_row"),
    ("brand", "brand_header"),
    ("tx_card", "_tx_card"),
    ("splitter", "_splitter"),
    ("open_row", "_open_row"),
)
WATCHED_EVENTS = {
    QEvent.Type.Resize: "Resize",
    QEvent.Type.Move: "Move",
    QEvent.Type.LayoutRequest: "LayoutRequest",
}


def rect_of(widget: QWidget) -> QRect:
    """控件在主窗口里的逻辑矩形（Move/Resize 两种事件都能用同一把尺子比）。"""
    if widget is None:
        return QRect()
    try:
        top = widget.window()
        return QRect(widget.mapTo(top, QPoint(0, 0)), widget.size())
    except RuntimeError:
        return QRect()


def delta(a: QRect, b: QRect) -> int:
    """两个矩形在 (x, y, w, h) 四个维度上的最大差。"""
    return max(abs(b.x() - a.x()), abs(b.y() - a.y()),
               abs(b.width() - a.width()), abs(b.height() - a.height()))


def stack_brief(skip: int = 2, frames: int = 3) -> str:
    """精简调用栈：只留 main_window.py / ui_*.py 的帧，末尾 frames 层。"""
    out = []
    for fr in traceback.extract_stack()[:-skip]:
        name = os.path.basename(fr.filename)
        if name.endswith(".py") and (name.startswith("main_window")
                                     or name.startswith("ui_")):
            out.append(f"{name}:{fr.lineno} {fr.name}")
    return " ← ".join(out[-frames:]) if out else "(无业务帧)"


class GeomSpy(QObject):
    """记录几何事件 + 调用栈。"""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.window = window
        self.targets: list[tuple[str, QWidget]] = []
        for name, attr in TRACK:
            w = getattr(window, attr, None)
            if isinstance(w, QWidget):
                self.targets.append((name, w))
                w.installEventFilter(self)
        self.rows: list[dict] = []
        self.enabled = False
        self.t0 = time.perf_counter()
        self._last_geom: dict[str, QRect] = {}

    def start(self) -> None:
        self.rows.clear()
        self._last_geom.clear()
        self.t0 = time.perf_counter()
        self.enabled = True

    def stop(self) -> None:
        self.enabled = False

    def detach(self) -> None:
        for _name, w in self.targets:
            try:
                w.removeEventFilter(self)
            except RuntimeError:
                pass

    def eventFilter(self, obj, event) -> bool:      # noqa: N802 (Qt 命名)
        if not self.enabled or event.type() not in WATCHED_EVENTS:
            return False
        name = next((n for n, w in self.targets if w is obj), "?")
        rect = rect_of(obj)
        prev = self._last_geom.get(name)
        # 只有在**几何真的变了**时才记（Move/Resize/LayoutRequest 会重复刷同一帧）
        if prev is not None and rect == prev:
            return False
        self._last_geom[name] = rect
        self.rows.append({
            "t": (time.perf_counter() - self.t0) * 1000.0,
            "name": name,
            "kind": WATCHED_EVENTS[event.type()],
            "rect": rect,
            "stack": stack_brief(),
        })
        return False


def pump(app, ms: float) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


def report(spy: GeomSpy, label: str) -> list[str]:
    """打印间隔 <GAP_MS 的连续几何变化（含栈），以及每个控件的最大单次跨度。"""
    out = [f"\n== {label} 几何事件 {len(spy.rows)} 条 =="]
    by_widget: dict[str, list[dict]] = {}
    for row in spy.rows:
        by_widget.setdefault(row["name"], []).append(row)

    hits = 0
    for name, rows in by_widget.items():
        for prev, cur in zip(rows, rows[1:]):
            gap = cur["t"] - prev["t"]
            if gap >= GAP_MS:
                continue
            d = delta(prev["rect"], cur["rect"])
            if d <= 0:
                continue
            hits += 1
            out.append(f"  {name:<9} t={cur['t']:7.1f}ms  间隔 {gap:4.1f}ms  "
                       f"Δ {d:4.0f}px   {prev['rect'].getRect()} → {cur['rect'].getRect()}")
            out.append(f"      前栈：{prev['stack']}")
            out.append(f"      后栈：{cur['stack']}")
    if not hits:
        out.append(f"  （没有间隔 <{GAP_MS:.0f}ms 的连续几何变化 —— 这一方向是干净的）")
    return out


def main() -> int:
    direction = "collapse"
    if "--dir" in sys.argv:
        direction = sys.argv[sys.argv.index("--dir") + 1]

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))

    from ui_anim import display_max_hz, frame_hz, set_frame_hz
    set_frame_hz(display_max_hz())

    from main_window import MainWindow
    window = MainWindow()
    window.show()
    window.apply_startup_state()
    pump(app, 360)

    spy = GeomSpy(window)
    lines: list[str] = [f"采样 {frame_hz()}Hz；时间窗 <{GAP_MS:.0f}ms 记为"
                        f"『同一拍里第二次落地』"]

    if direction == "collapse":
        window.btn_expand.click()          # 先进入最大化，再测退出
        pump(app, SETTLE_MS)
    else:
        pump(app, 60)

    # ── 落定路径 no-op 检查：包一层 _on_rx_done ────────────────────────────
    before: dict[str, QRect] = {}
    after: dict[str, QRect] = {}
    real_done = window._on_rx_done

    def _wrapped_done() -> None:
        for name, w in spy.targets:
            before[name] = rect_of(w)
        real_done()
        for name, w in spy.targets:
            after[name] = rect_of(w)

    # ⚠️ 补间是在第一次展开时**构造**的，``on_finished`` 在构造那一刻就把绑定方法
    #    存进去了 —— 只改 window._on_rx_done 属性是没用的，必须连补间上的那个
    #    ``_on_finished`` 一起换掉。
    window._on_rx_done = _wrapped_done
    tween = window._rx_tween
    original_finished = tween._on_finished if tween is not None else None
    if tween is not None:
        tween._on_finished = _wrapped_done

    spy.start()
    window.btn_expand.click()
    pump(app, SETTLE_MS)
    spy.stop()
    window._on_rx_done = real_done
    if tween is not None:
        tween._on_finished = original_finished

    lines += report(spy, f"接收区{direction}")

    lines.append("\n== 落定路径 no-op 检查（_on_rx_done 前后，要求 diff == 0）==")
    worst = 0
    if not before:
        lines.append("  （没抓到落定帧 —— _on_rx_done 没被调用？）")
    else:
        for name, _w in spy.targets:
            a, b = before.get(name), after.get(name)
            if a is None or b is None:
                continue
            d = delta(a, b)
            worst = max(worst, d)
            flag = "" if d == 0 else "   ← 落定又动了一下"
            lines.append(f"  {name:<9} {a.getRect()} → {b.getRect()}  Δ {d:4.0f}px{flag}")
        lines.append(f"  → 最大 diff {worst}px（{'PASS' if worst == 0 else 'FAIL'}）")

    print("\n".join(lines))
    spy.detach()
    window.setGeometry(0, 0, 1134, 820)
    window.close()
    return 0 if worst == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
