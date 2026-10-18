# -*- coding: utf-8 -*-
r"""J1 判据自检：`_probe_anim_frames` 的 "速度 > 中位×4" 在**理想曲线**上就会亮灯。

结论先写在这儿：这不是代码跳，是阈值比曲线本身的形状还低。

`_probe_anim_frames.analyse` 的 J1 是 ``v > max(med * 4, 0.35)``，med 是该序列
**移动帧**的速度中位数。但转场用的是 ``ui_anim.STANDARD``（cubic-bezier(.2,0,0,1)）
—— 这条曲线的速度分布**极不均匀**：起步猛、尾巴长，峰值速度是中位速度的 5~7 倍
（取决于步长）。也就是说：**一条完全平滑、没有任何跳变的 STANDARD 动画，也会被判
J1 违例**。本脚本把这条曲线按不同步长采样，算出"理想情况下的 max/中位"，就是 J1
阈值必须大于的下界。

顺带给出另一个口径：单步位移占整段行程的比例。理想曲线的理论占比是
``4.04 × 步长 / 时长``（4.04 是 STANDARD 的峰值斜率），跟采样密度无关 —— 拿它当
判据就不会被"采样比送值细"这件事带跑。

用法：python devtools/_probe_j1_threshold.py
"""
from __future__ import annotations

import os
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ui_anim import STANDARD                                     # noqa: E402

DURATION_MS = 260.0       # 接收区转场时长（main_window._RX_TRANSITION_MS）
CURVE_PEAK_SLOPE = 4.04   # STANDARD 峰值斜率（数值微分）


def steps(step_ms: float, duration_ms: float = DURATION_MS) -> list[float]:
    """按固定步长把曲线切成段的**单步进度增量**（0~1 归一，不含像素）。"""
    total = int(round(duration_ms / step_ms))
    out: list[float] = []
    prev = STANDARD(0.0)
    for i in range(1, total + 1):
        u = min(1.0, i * step_ms / duration_ms)
        cur = STANDARD(u)
        out.append(cur - prev)
        prev = cur
    return out


def main() -> int:
    print(f"曲线 = ui_anim.STANDARD（cubic-bezier(.2,0,0,1)），时长 {DURATION_MS:.0f}ms")
    print("J1 现行阈值 = 速度 > 中位×4\n")
    print(f"{'步长':>8}  {'帧数':>5}  {'理想 max/中位':>14}  {'J1 会不会亮':>12}"
          f"  {'单步/行程 理论':>15}")
    print("-" * 74)
    worst = 0.0
    for step_ms in (32.0, 16.0, 8.0, 6.1, 4.0):
        deltas = steps(step_ms)
        moving = [d for d in deltas if d > 1e-6]
        med = statistics.median(moving)
        ratio = max(deltas) / med
        worst = max(worst, ratio)
        theory = min(1.0, CURVE_PEAK_SLOPE * step_ms / DURATION_MS)
        print(f"{step_ms:7.1f}ms  {len(deltas):5d}  {ratio:13.2f}×  "
              f"{'必然亮灯' if ratio > 4 else '不亮':>12}  {theory * 100:14.1f}%")
    print("-" * 74)
    print(f"→ 理想无跳变动画的 max/中位 最高 {worst:.2f}×，"
          f"J1 的 4× 门槛**在下界之下**：阈值至少要 > {worst:.1f}× 才有意义；")
    print("  更稳的判据是看**单步位移占行程的比例**（末列），理论值 = 4.04×步长/时长，"
          "再给 25% 余量。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
