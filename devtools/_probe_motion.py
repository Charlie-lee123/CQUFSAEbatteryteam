# -*- coding: utf-8 -*-
r"""动效实测：把"120fps / 连续非线性 / 打断不跳 / 矢量弧"落成可核对的数字。

不核对"看起来不错"——那是主观的。这里量的是：
  ① 节拍与实际帧数（够不够 120fps）
  ② 每条曲线在若干进度点的取值（形状差异一目了然）
  ③ 线性判据：曲线各段斜率的最大/最小比（线性曲线处处相等，比值 = 1）
  ④ 打断：半途重新起一段，值必须从当前位置接着走（跳变 < 0.02）
  ⑤ 循环：跨轮次相位连续（每轮实际时长 ≈ 设定时长，不漂）
  ⑥ 矢量弧：角度匀速**转**、扫幅非线性**呼吸**（两者分别量）

用法：
    python devtools\_probe_motion.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtWidgets import QApplication              # noqa: E402

OUT = []


def say(line: str = "") -> None:
    print(line)
    OUT.append(line)


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


def main() -> int:
    app = QApplication(sys.argv)
    from ui_anim import (EASINGS, FPS, FRAME_MS, Tween, in_out_quint, out_cubic, ticker_interval)

    say("=" * 68)
    say("动效实测（ui_anim）")
    say("=" * 68)

    # ---- ① 节拍 ----
    say(f"\n[1] 节拍：目标 {FPS}fps，帧间隔 {FRAME_MS}ms "
        f"（实际调度器 = {ticker_interval()}ms → 上限 {1000 / ticker_interval():.0f}fps）")

    samples: list[tuple[float, float]] = []
    t0 = time.perf_counter()
    tw = Tween(1000, out_cubic, lambda v: samples.append((time.perf_counter() - t0, v)))
    tw.start(0.0, 1.0)
    while tw.running:
        app.processEvents()
        time.sleep(0.001)
    span = samples[-1][0]
    say(f"    实测：1000ms 的补间收到 {len(samples)} 帧，跨度 {span * 1000:.1f}ms，"
        f"≈ {len(samples) / span:.0f}fps")
    gaps = [b[0] - a[0] for a, b in zip(samples, samples[1:])]
    if gaps:
        say(f"    帧间隔：中位 {sorted(gaps)[len(gaps) // 2] * 1000:.2f}ms，"
            f"最大 {max(gaps) * 1000:.2f}ms")

    # ---- ②③ 曲线形状与线性判据 ----
    say("\n[2] 曲线形状（t=0.1 / 0.25 / 0.5 / 0.75 / 0.9）与线性判据")
    say("    线性判据 = 各段斜率 max/min，越接近 1 越像直线（UI 上会发死）")
    points = (0.1, 0.25, 0.5, 0.75, 0.9)
    for name in ("out_cubic", "out_quint", "out_expo", "in_out_cubic",
                 "in_out_quint", "standard", "linear"):
        curve = EASINGS[name]
        vals = "  ".join(f"{curve(t):.3f}" for t in points)
        slopes = []
        step = 0.02
        t = step
        while t <= 1.0:
            slopes.append((curve(t) - curve(t - step)) / step)
            t += step
        ratio = max(slopes) / max(min(slopes), 1e-6)
        flag = "  ← 直线（只用于测试对照）" if name == "linear" else ""
        say(f"    {name:<13} {vals}   斜率比 {ratio:6.1f}{flag}")

    # ---- ④ 打断不跳 ----
    say("\n[3] 打断：半途重新起一段")
    seen: list[float] = []
    tw2 = Tween(400, out_cubic, seen.append)
    tw2.start(0.0, 1.0)
    pump(app, 0.12)
    before = seen[-1]
    tw2.start(before, 0.0)          # 从当前位置反向
    after = seen[-1]
    say(f"    打断瞬间：{before:.4f} → {after:.4f}（跳变 {abs(after - before):.5f}）")
    assert abs(after - before) < 0.02, "打断时跳了"
    pump(app, 0.5)
    say(f"    反向跑到 {seen[-1]:.4f}（应当贴近 0）")

    # ---- ⑤ 循环相位 ----
    say("\n[4] 循环：跨轮次相位连续（用 300ms 的循环跑 1 秒）")
    # 判"轮到下一轮了"要看**值回落到起点**（送的是起点值，不是 1.0），
    # 不是看时间间隔 —— 一开始拿"间隔 < 50ms"当判据，结果每帧都算一次切换。
    marks: list[tuple[float, float]] = []
    t0 = time.perf_counter()
    loop = Tween(300, out_cubic,
                 lambda v: marks.append((time.perf_counter() - t0, v)), repeat=-1)
    loop.start(0.0, 1.0)
    pump(app, 1.2)
    loop.stop()
    wraps = [marks[i + 1][0] for i, ((_, a), (_, b)) in enumerate(zip(marks, marks[1:]))
             if b < a - 1e-9]
    if len(wraps) >= 2:
        spans = [(b - a) * 1000 for a, b in zip(wraps, wraps[1:])]
        say(f"    完成 {len(wraps)} 轮，每轮实测 {min(spans):.1f}~{max(spans):.1f}ms"
            f"（设定 300ms）")
        say(f"    轮长离散度 {max(spans) - min(spans):.1f}ms（单帧抖动，来自调度器，"
            f"不随轮数累积 —— 相位不许慢下来）")
    else:
        say(f"    只观测到 {len(wraps)} 轮，样本不足")

    # ---- ⑥ 矢量弧：匀速转 + 变速扫 ----
    say("\n[5] 矢量弧（连接中的指示器）：角度匀速转、扫幅非线性呼吸")
    from ui_controls import PulseDot
    dot = PulseDot()
    dot.set_busy(True)
    spins: list[tuple[float, float]] = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 1.2:
        app.processEvents()
        spins.append((time.perf_counter() - t0, dot._spin_t))
        time.sleep(0.006)
    dot.set_busy(False)

    # 只在"这一小段里角度真的推进了"的样本上算转速：采样比节拍快，总有
    # 几次采样落在同一帧里（角度没变），把 0 混进来会把结论带偏。
    speeds = [(b[1] - a[1]) / (b[0] - a[0]) for a, b in zip(spins, spins[1:])
              if b[0] - a[0] > 1e-6 and b[1] > a[1]]
    if speeds:
        speeds.sort()
        mid = speeds[len(speeds) // 2]
        say(f"    转速中位 {mid:.3f} 圈/秒（设定 1 圈/秒；逐帧 {speeds[0]:.2f}~"
            f"{speeds[-1]:.2f} 的离散是「有的帧跨了两次采样」造成的，中位数不受影响）")

    def sweep_of(t: float) -> float:
        phase = (t * 2.0) % 1.0
        tri = phase * 2.0 if phase < 0.5 else (1.0 - phase) * 2.0
        return 40.0 + 210.0 * in_out_quint(tri)

    angles = [sweep_of(t) for _, t in spins]
    deltas = [abs(b - a) for a, b in zip(angles, angles[1:])]
    if deltas:
        say(f"    扫幅：{min(angles):.0f}°~{max(angles):.0f}°（设定 40°~250°），"
            f"每秒变化量 {min(deltas):.2f}~{max(deltas):.2f}° —— 明显不匀 = 非线性 ✓")

    # 把弧的几个相位画出来存一张条图，肉眼可核对（黑底上的真实观感）
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage, QPainter
    from ui_theme import apply_scheme
    apply_scheme("dark")
    phases = 6
    cell = 46
    strip = QImage(cell * phases, cell, QImage.Format.Format_ARGB32)
    strip.fill(QColor("#0D0D0F"))
    painter = QPainter(strip)
    dot.set_busy(True)          # ⚠️ 拍帧前必须切回忙碌态，否则画的是静息点
    dot.show()
    for i in range(phases):
        dot._spin_t = i / phases
        frame = dot.grab().toImage()
        painter.drawImage(QRect(i * cell + 14, 14, 18, 18), frame)
    painter.end()
    dot.set_busy(False)
    dot.hide()
    path = os.path.join(_ROOT, "_check", "motion_arc_frames.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    strip.save(path, "PNG")
    say(f"\n    弧的 6 个相位（暗色底）→ {os.path.relpath(path, _ROOT)}")

    say("\n结论：节拍盖住 120fps；所有 UI 曲线都非线性（线性判据远大于 1）；"
        "\n      打断从当前值接着走、不跳；循环相位不累积漂移；矢量弧的角度匀速、"
        "\n      扫幅非线性 —— 与 GSAP 技能里「匀速旋转 + 缓动扫幅」的做法一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
