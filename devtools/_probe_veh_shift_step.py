# -*- coding: utf-8 -*-
"""探针（lead 2026-11-07）：量化「车号选中 → 其他元素下移」的单帧最大位移。

只读、不依赖 GUI：直接复用 ui_settings._veh_shift_ease，按真实帧间隔模型
（8/14/25/40ms，对应 125/70/40/25fps）模拟 holder 高度逐帧增量，报告最大单帧步长。

目的：确认「改近匀速缓动 + 延长到 480ms」后，典型位移（Δ≈138px，3 模组差）的
单帧步长降到 ~8px 以内（之前用 EASE(1.9× 峰值斜率) 实测 16px，肉眼可见顿挫）。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ui_settings import _veh_shift_ease  # noqa: E402


def max_step(delta: float, duration_ms: int, frame_ms: float) -> float:
    """模拟逐帧 holder 高度，返回相邻两帧最大 |Δh|。"""
    prev_h = 0.0
    max_d = 0.0
    ms = 0.0
    while ms <= duration_ms + 1e-9:
        p = min(1.0, ms / float(duration_ms))
        h = delta * _veh_shift_ease(p)
        if ms > 0.0:
            max_d = max(max_d, abs(h - prev_h))
        prev_h = h
        ms += frame_ms
    return max_d


def main() -> int:
    duration = 480
    # Δ = _VEH_PITCH(46) × 模组数差：1/3/5/7 模组差 ≈ 46/138/230/322
    deltas = [46, 138, 230, 322]
    frames = [8, 14, 25, 40]
    print(f"duration={duration}ms  ease=近匀速(峰值斜率≈1.14)")
    header_cells = [f"{f}ms帧".rjust(10) for f in frames]
    print(f"{'Δ(px)':>6} | " + " | ".join(header_cells))
    worst = 0.0
    for d in deltas:
        row = f"{d:>6} | "
        cells = []
        for f in frames:
            step = max_step(d, duration, f)
            worst = max(worst, step)
            cells.append(f"{step:>8.1f}px")
        print(row + " | ".join(cells))
    print(f"\n最差单帧步长 ≈ {worst:.1f}px（典型 Δ=138 @25ms 目标 ≤ ~8px）")
    # 验收：典型与较大位移在 40fps(25ms) 下不超过 12px（之前是 16px 且中段尖峰）
    ok = max_step(138, duration, 25) <= 12.0 and max_step(230, duration, 25) <= 14.0
    print("验收(Δ138@25ms≤12, Δ230@25ms≤14):", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
