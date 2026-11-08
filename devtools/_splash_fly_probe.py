"""Plan B 飞行段流畅度分析（工具 venv，numpy+PIL）：

1. 交接帧差异：揭示段合成内容(t=2.4667, 第148帧) vs 飞行段首帧内容(t=2.47, rect0)。
   两者位置相同，若内容差异显著 → 交接瞬间有一次可见的"跳"。
2. FLY_EASE 速度剖面 + 候选曲线对比（首帧增量、峰值位置、尾段是否有冻结帧）。

用法：
    F:/WorkBuddyTools/venv/Scripts/python.exe devtools/_splash_fly_probe.py
"""

from __future__ import annotations

import importlib.util
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("sr", os.path.join(ROOT, "devtools", "_splash_render.py"))
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)


def clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def bezier(x1, y1, x2, y2):
    def ease(p):
        p = clamp01(p)
        lo, hi = 0.0, 1.0
        for _ in range(28):
            mid = (lo + hi) / 2.0
            bx = 3 * (1 - mid) ** 2 * mid * x1 + 3 * (1 - mid) * mid * mid * x2 + mid ** 3
            lo, hi = (mid, hi) if bx < p else (lo, mid)
        t = (lo + hi) / 2.0
        return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t ** 3
    return ease


def part1_handoff():
    sc = sr.Scene("light", "B")
    fly = sr.PLANS["B"]["fly"]
    t_pre = (int(fly[0] * sr.FPS) - 1) / sr.FPS        # 揭示段最后一帧
    img_pre = np.array(sc.frame(t_pre), dtype=np.int16)
    img_fly0 = np.array(sc.frame(fly[0]), dtype=np.int16)   # e=0 → rect0 精确
    d = np.abs(img_pre - img_fly0)
    # 只看 lockup 区域（rect0 范围，略放大）
    x0, y0, w, h = sc.rect0
    box = d[int(y0) - 8:int(y0 + h) + 8, int(x0) - 8:int(x0 + w) + 8]
    nz = (box.max(axis=2) > 8).sum()
    print("== 交接帧差异（同一位置、两套内容）==")
    print(f"t_pre={t_pre:.4f}  区域 {box.shape[1]}x{box.shape[0]}")
    print(f"mean|d|={box.mean():.3f}/255  p99={np.percentile(box, 99):.1f}  "
          f"max={box.max()}  差异>8 的像素数={nz} ({nz / box[..., 0].size:.2%})")
    # 差异热区行/列分布（判断是不是集中在文字/图标边缘）
    colsum = box.max(axis=2).sum(axis=0)
    rowsum = box.max(axis=2).sum(axis=1)
    print(f"差异列范围: {np.nonzero(colsum > box.shape[0] * 0.5)[0][[0, -1]] if (colsum > box.shape[0]*0.5).any() else '无'}")
    return sc


def part2_profiles():
    fly = sr.PLANS["B"]["fly"]
    n = int(round((fly[1] - fly[0]) * sr.FPS))
    sc = sr.Scene("light", "B")
    # 中心位移总量（px）
    c0 = (sc.rect0[0] + sc.rect0[2] / 2, sc.rect0[1] + sc.rect0[3] / 2)
    c1 = (sc.rect1[0] + sc.rect1[2] / 2, sc.rect1[1] + sc.rect1[3] / 2)
    import math
    travel = math.hypot(c1[0] - c0[0], c1[1] - c0[1])
    print(f"\n== 飞行段 {fly[0]}→{fly[1]} 共 {n} 帧，中心位移 {travel:.0f}px ==")

    cands = {
        "FLY_EASE(现行)": bezier(0.28, 0.16, 0.55, 1.0),
        "A 0.30/0.09/0.22": bezier(0.30, 0.09, 0.22, 1.0),
        "B 0.26/0.06/0.20": bezier(0.26, 0.06, 0.20, 1.0),
        "C 0.33/0.10/0.25": bezier(0.33, 0.10, 0.25, 1.0),
    }
    print(f"{'曲线':<18}{'首帧px':>8}{'峰值px':>8}{'峰值帧':>7}{'首/峰':>7}{'末5帧px':>12}{'冻结帧':>7}")
    for name, fn in cands.items():
        es = [fn(i / n) for i in range(n + 1)]
        dpx = [(es[i] - es[i - 1]) * travel for i in range(1, n + 1)]
        peak = max(dpx)
        peak_i = dpx.index(peak) + 1
        first = dpx[0]
        tail = dpx[-5:]
        frozen = sum(1 for v in dpx if v < 0.5)
        print(f"{name:<18}{first:>8.1f}{peak:>8.1f}{peak_i:>7d}{first/peak:>7.2f}"
              f"{'/'.join('%.1f' % v for v in tail):>12}{frozen:>7d}")


if __name__ == "__main__":
    sc = part1_handoff()
    del sc
    part2_profiles()
