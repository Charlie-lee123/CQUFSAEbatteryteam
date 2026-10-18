# -*- coding: utf-8 -*-
r"""S4 阳性对照：串口开关**展开方向**飞行途中被功能列宽度变化"拽回"的量化。

问题（用户报 + lead 定位）
--------------------------
``_rx_prepare`` 在展开方向把 ``_rx_switch_rail`` 留 None → ``_rx_place_switch``
回退到 ``_rx_rail_rect()`` **逐帧现算**落点。而 ``_on_rx_transition`` 每帧都在
``setFixedWidth(rail_w)`` 改功能列宽（rail 右对齐时它的左沿 x 随之左移），开关
追着一个正在移动的落点飞，末端被往回拽 —— 实测约 14px。

判据口径（lead 2026-10-19 裁定）
--------------------------------
hook ``_rx_place_switch``：原函数跑完**当场**读 ``row.geometry()`` —— 那就是它
内部算出来的 pos。⛔ 不事后 ``mapToGlobal`` 重测（那量的是另一条路径，且飞行段
控件挂在窗口上、任何布局扰动都会让两种读法对不上）。

指标
----
对飞行段（p: 0→1）记录到的 pos 序列，按净位移定方向（展开向右、收起向左），
**反着走的步**算回抽（与 ``_probe_full_anim_audit`` 的 P2 同口径）：
  · **回抽合计** = 所有反向步的幅度之和；
  · **最大单步回抽** = 最狠的那一步。
两者修前预期 ≈14px（起手两帧先被拽向左侧的窄列，再折返飞向落点），修后
（展开方向冻结 rail 落点）预期 0（1px 取整噪声内）。

⚠️ 缓存方案只在**第二次及以后的展开**生效（首次展开时缓存还是 None，走就地
回退）。所以探针顺序是：展开(热身，落定时写缓存) → 收起 → 展开(**测量**)。
未修复的代码两次展开行为一致，阳性对照不受影响。

用法：python devtools/_probe_rx_switch_retract.py [--dir expand|collapse|both]
      [--out _check/xxx.txt]
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtWidgets import QApplication                      # noqa: E402

OUT_PATH = os.path.join(_ROOT, "_check", "rx_switch_retract.txt")
SETTLE_MS = 750.0
WARMUP_MS = 750.0
LINES: list[str] = []


def say(line: str = "") -> None:
    print(line)
    LINES.append(line)


def pump(app, ms: float) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


class SwitchSpy:
    """hook ``_rx_place_switch``：原函数跑完当场读它算出的 pos。"""

    def __init__(self, window) -> None:
        self.window = window
        self.rows: list[dict] = []          # {t, p, x, y, w, on_window}
        self._orig = None
        self._t0 = 0.0
        self.enabled = False

    def _hooked(self, p: float) -> None:
        self._orig(p)
        if not self.enabled:
            return
        row = self.window._open_row
        try:
            g = row.geometry()
            self.rows.append({
                "t": (time.perf_counter() - self._t0) * 1000.0,
                "p": float(p),
                "x": g.x(), "y": g.y(), "w": g.width(),
                "on_window": row.parentWidget() is self.window,
            })
        except RuntimeError:
            pass

    def __enter__(self):
        self._orig = self.window._rx_place_switch
        self.window._rx_place_switch = self._hooked
        return self

    def __exit__(self, *exc):
        self.window._rx_place_switch = self._orig
        return False

    def start(self) -> None:
        self.rows.clear()
        self._t0 = time.perf_counter()
        self.enabled = True

    def stop(self) -> None:
        self.enabled = False


def _retraction(rows: list[dict]) -> tuple[float, float, int]:
    """飞行段的 (回抽合计, 最大单步回抽, 样本数)。只统计挂在窗口上的帧。

    方向按净位移定（和 _probe_full_anim_audit 的 P2 同口径）：展开向右、收起
    向左，**反着走的步**才算回抽。量化噪声 ±1px 不计（阈值放到判据里）。
    """
    flying = [r for r in rows if r["on_window"]]
    if len(flying) < 2:
        return 0.0, 0.0, len(flying)
    xs = [r["x"] for r in flying]
    direction = 1 if xs[-1] >= xs[0] else -1
    back_steps = [b - a for a, b in zip(xs, xs[1:])
                  if (b - a) * direction < 0]
    total = float(sum(abs(d) for d in back_steps))
    worst = float(max((abs(d) for d in back_steps), default=0.0))
    return total, worst, len(flying)


def _seq_line(rows: list[dict], key: str) -> str:
    return " ".join(str(r[key]) for r in rows)


def measure(app, window, spy: SwitchSpy, direction: str) -> None:
    """跑一个方向的**测量段**（调用前窗口应处在该方向的起点态）。"""
    spy.start()
    window.btn_expand.click()
    pump(app, SETTLE_MS)
    spy.stop()
    total, worst, n = _retraction(spy.rows)
    flying = [r for r in spy.rows if r["on_window"]]
    say(f"\n== {direction}：飞行段 {n} 帧 ==")
    say(f"  x 序列（hook 当场取的内部 pos）: {_seq_line(flying, 'x')}")
    xs = [r["x"] for r in flying]
    if len(xs) >= 2:
        steps = [b - a for a, b in zip(xs, xs[1:])]
        say(f"  单步 Δx: {' '.join(f'{s:+d}' for s in steps)}")
    say(f"  y 序列: {_seq_line(flying, 'y')}")
    say(f"  w 序列: {_seq_line(flying, 'w')}")
    say(f"  ▶ 回抽合计 {total:.0f}px   最大单步回抽 {worst:.0f}px")
    verdict = "PASS" if worst <= 1 and total <= 2 else "FAIL"
    say(f"  ▶ 判据（单步回抽 ≤1px 且合计 ≤2px）: {verdict}")
    globals()["_LAST_VERDICT"] = verdict


def main() -> int:
    direction = "both"
    if "--dir" in sys.argv:
        direction = sys.argv[sys.argv.index("--dir") + 1]
    out = OUT_PATH
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
        if not os.path.isabs(out):
            out = os.path.join(_ROOT, out)

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
    pump(app, 500)

    say("=" * 78)
    say(f"S4 开关回抽探针   采样节拍 = {frame_hz()}Hz   方向 = {direction}")
    say("hook _rx_place_switch 当场读内部 pos（不事后 mapToGlobal）")
    say("=" * 78)

    verdicts: list[str] = []
    with SwitchSpy(window) as spy:
        if direction in ("expand", "both"):
            # 热身：先完整展开一次（落定时会把最大化态 rail 落点写进缓存），
            # 再收起回到普通态 —— 测量段才是"缓存已就绪"的那一次展开。
            window.btn_expand.click()
            pump(app, WARMUP_MS)
            window.btn_expand.click()          # 收起，回普通态
            pump(app, WARMUP_MS)
            measure(app, window, spy, "展开方向（测量段）")
            verdicts.append(globals().get("_LAST_VERDICT", "FAIL"))
        if direction in ("collapse", "both"):
            if direction == "collapse":
                window.btn_expand.click()      # 先进入最大化
                pump(app, WARMUP_MS)
            measure(app, window, spy, "收起方向（对照）")
            verdicts.append(globals().get("_LAST_VERDICT", "FAIL"))

    say("\n" + "=" * 78)
    say(f"汇总: {' / '.join(verdicts)}")
    text = "\n".join(LINES)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    say(f"产物 → {out}")

    window.setGeometry(0, 0, 1134, 820)
    window.close()
    return 0 if all(v == "PASS" for v in verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
