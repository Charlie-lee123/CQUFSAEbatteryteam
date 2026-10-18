# -*- coding: utf-8 -*-
r"""逐帧动画体检（2.1 后新增）：在**补间节拍**上取样，判断有没有真正的跳变。

为什么要另写一个：`_probe_motion_jumps.py` 用"看门狗线程 + processEvents"采样，
采样间隔（4~20ms）和动画帧（8ms）不同步 —— 一帧采样不到就会把两帧的运动并成
一次大位移，看起来像跳变；反过来连续两帧静止，也会把正常的快速运动标成"孤立
阶跃"。这里把取样挂在 `ui_anim._Ticker._tick` 上：**每推进一帧补间就取一次**，
采样与动画严格同拍，剩下的位移就都是真实的。

判据（对每个被跟踪的几何量）：
  · J1 单帧阶跃：某帧位移 > max(12px, 6×该序列 |位移| 中位数) —— 真正的瞬移
  · J2 起点跳：第一帧位移 > 3×中位数（动画从错误起点起跑）
  · J3 终点超调：最后一帧位移 > 3×中位数（落定时被夹/弹一下）
  · J4 中途可见性翻转：控件在"还在动"的阶段突然显隐

用法：python devtools/_probe_anim_frames.py
产物：_check/anim_frames.txt（逐帧原始数据）
"""
from __future__ import annotations

import os
import statistics
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import QPoint                                # noqa: E402
from PySide6.QtWidgets import QApplication                      # noqa: E402

TRACK = (("rx_card", "_rx_card"), ("txt_rx", "txt_rx"), ("rail", "_rx_rail"),
         ("open_row", "_open_row"), ("tx_card", "_tx_card"),
         ("brand", "brand_header"), ("config", "_config_card"),
         ("opts", "_rx_opts_row"), ("search", "_search_bar"))
RAW = os.path.join(_ROOT, "_check", "anim_frames.txt")
SETTLE_MS = 520.0


class FrameTap:
    """把取样挂到补间节拍上（每次 tick 之后取一帧）。"""

    def __init__(self, app, window, target=None):
        self.app = app
        self.window = window
        self.rows = []
        self.target = target or window
        self._orig = None

    def _grab(self) -> None:
        t = (time.perf_counter() - self.t0) * 1000.0
        row = [round(t, 1)]
        for _name, attr in TRACK:
            w = getattr(self.window, attr, None)
            if w is None:
                row.extend([0, 0, 0, 0, 0])
                continue
            try:
                p = w.mapToGlobal(QPoint(0, 0))
                g = w.geometry()
                row.extend([p.x(), p.y(), g.width(), g.height(), 1 if w.isVisible() else 0])
            except RuntimeError:
                row.extend([0, 0, 0, 0, 0])
        self.rows.append(row)

    def __enter__(self):
        import ui_anim
        ticker = ui_anim._ticker()
        self._orig = ticker._tick

        def _patched():
            self._orig()
            self._grab()

        ticker._tick = _patched
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        import ui_anim
        ui_anim._ticker()._tick = self._orig
        return False

    def run(self, action, settle_ms: float = SETTLE_MS) -> list[list[float]]:
        self.rows.clear()
        self.t0 = time.perf_counter()
        action()
        # 让事件循环把补间跑完（不自己 sleep 采样，取样全在 tick 里）
        deadline = time.perf_counter() + settle_ms / 1000.0
        while time.perf_counter() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.app.processEvents()
        return self.rows


def analyse(name: str, rows: list[list[float]]) -> list[str]:
    """按**速度**判跳变：px/ms 超过移动段中位速度的 4 倍才算瞬移。

    为什么不看"单帧位移"：补间是 125Hz，但一帧重绘可能拖到 30~40ms，同样一段
    位移摊在更长的 dt 上，单帧位移自然变大 —— 那是慢帧，不是跳变。除以 dt
    之后，慢帧的速度不变，真正的瞬移才会冒出来（跳变 = 一瞬间走完一大段）。
    """
    span = max(0.1, rows[-1][0] - rows[0][0])
    fps = (len(rows) - 1) / span * 1000.0
    out = [f"\n== {name} ==  帧数 {len(rows)}（≈{fps:.0f}fps，{span:.0f}ms）"]
    if len(rows) < 3:
        out.append("  （没有动画帧 —— 这一段可能根本没起补间）")
        return out
    for i, (short, _attr) in enumerate(TRACK):
        base = 1 + i * 5
        speeds, deltas, dts = [], [], []
        for a, b in zip(rows, rows[1:]):
            d = max(abs(b[base + k] - a[base + k]) for k in range(4))
            dt = max(0.1, b[0] - a[0])
            deltas.append(d)
            dts.append(dt)
            speeds.append(d / dt)
        moving = [v for v in speeds if v * 0.1 > 0.02]
        if not moving:
            continue
        med = statistics.median(moving)
        flags = []
        for idx, v in enumerate(speeds):
            if v > max(med * 4, 0.35):        # 0.35px/ms ≈ 2.8px/8ms
                flags.append(f"J1@帧{idx + 1} {v:.2f}px/ms（{deltas[idx]:.0f}px/"
                             f"{dts[idx]:.0f}ms，中位 {med:.2f}）")
        travel = max(abs(rows[-1][base + k] - rows[0][base + k]) for k in range(4))
        if deltas and deltas[0] > max(travel * 0.35, 12):
            flags.append(f"J2 起点跳 {deltas[0]:.0f}px")
        if deltas and deltas[-1] > max(travel * 0.35, 12):
            flags.append(f"J3 终点跳 {deltas[-1]:.0f}px")
        for a, b in zip(rows, rows[1:]):
            if a[base + 4] != b[base + 4]:
                # 缩到 1/8 才隐藏是既定策略（Reveal 的阈值），不算跳变
                small = (a[base + 2] <= max(24, rows[0][base + 2] // 8)
                         and a[base + 3] <= max(24, rows[0][base + 3] // 8))
                if not small:
                    flags.append(f"J4 显隐翻转 @{b[0]:.0f}ms → "
                                 f"{'可见' if b[base + 4] else '隐藏'}"
                                 f"（当时 {a[base + 2]}x{a[base + 3]}）")
                break
        if flags:
            out.append(f"  {short}: " + "; ".join(flags))
    if len(out) == 1:
        out.append("  ✅ 全部连续（没有超阈值的瞬移）")
    return out


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))

    # ⛔ 采样率拉到**屏幕帧率**：补间节拍就是采样节拍（取样挂在 _Ticker._tick 上），
    #    节拍低于屏幕刷新率时，一切"每帧位移"都会被采样成台阶，看起来像跳变。
    #    这里把帧率设成显示器上限（本机 165Hz → 6ms/帧），量的才是用户眼睛看到的。
    from ui_anim import display_max_hz, frame_hz, set_frame_hz
    set_frame_hz(display_max_hz())
    print(f"采样节拍 = {frame_hz()}Hz（显示器上限 {display_max_hz()}Hz）")

    from main_window import MainWindow
    window = MainWindow()
    window.show()
    window.apply_startup_state()
    for _ in range(120):
        app.processEvents()
        time.sleep(0.003)

    lines = []
    with FrameTap(app, window) as tap:
        lines += analyse("接收区展开", tap.run(lambda: window.btn_expand.click()))
        for _ in range(120):
            app.processEvents()
            time.sleep(0.003)
        lines += analyse("接收区退出", tap.run(lambda: window.btn_expand.click()))
        for _ in range(120):
            app.processEvents()
            time.sleep(0.003)
        lines += analyse("发送区展开", tap.run(lambda: window.btn_minimize.click()))
        for _ in range(120):
            app.processEvents()
            time.sleep(0.003)
        lines += analyse("发送区折叠", tap.run(lambda: window.btn_minimize.click()))
        for _ in range(120):
            app.processEvents()
            time.sleep(0.003)
        lines += analyse("搜索条展开", tap.run(lambda: window.btn_search.click()))
        for _ in range(90):
            app.processEvents()
            time.sleep(0.003)
        lines += analyse("搜索条收起", tap.run(lambda: window.btn_search.click()))
        for _ in range(90):
            app.processEvents()
            time.sleep(0.003)
        other = "light" if window._theme_mode == "dark" else "dark"
        lines += analyse("主题切换", tap.run(lambda: window._animate_theme(other)))

    text = "\n".join(lines)
    print(text)
    with open(RAW, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"\n逐帧数据 → {RAW}")

    # 收尾：几何搬回 + 关窗（探针不碰用户配置）
    window.setGeometry(0, 0, 1134, 820)
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
