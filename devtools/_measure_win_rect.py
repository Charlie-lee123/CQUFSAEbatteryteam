# -*- coding: utf-8 -*-
"""逐帧测量应用窗口矩形，量化最大化/还原动画的运动曲线。

原理：桌面背景是深色（WorkBuddy IDE），应用窗口是浅色大块。
对每一帧在灰度图上取 >180 的亮像素做连通范围（行列投影的紧凑包围盒），
打印矩形随帧号的变化 —— 静止=冻结，一次跳变=闪。
"""
import os
import sys

import cv2
import numpy as np


def win_rect(gray: np.ndarray):
    bright = gray > 180
    cols = bright.sum(axis=0)
    rows = bright.sum(axis=1)
    if cols.max() < 50:
        return None
    xs = np.where(cols > 30)[0]
    ys = np.where(rows > 30)[0]
    if len(xs) == 0 or len(ys) == 0:
        return None
    # 取最大连续段（排除任务栏等零散亮块）
    def largest_run(idx):
        if len(idx) == 0:
            return None
        splits = np.where(np.diff(idx) > 12)[0]
        segs = np.split(idx, splits + 1)
        return max(segs, key=len)
    cx = largest_run(xs)
    cy = largest_run(ys)
    if cx is None or cy is None:
        return None
    return int(cx[0]), int(cy[0]), int(cx[-1]), int(cy[-1])


def main() -> int:
    frames_dir = sys.argv[1]
    lo, hi = int(sys.argv[2]), int(sys.argv[3])
    files = sorted(os.listdir(frames_dir))
    prev = None
    for name in files:
        n = int(name.split("_")[1].split(".")[0])
        if not (lo <= n <= hi):
            continue
        img = cv2.imdecode(np.fromfile(os.path.join(frames_dir, name),
                                       dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        r = win_rect(img)
        if r is None:
            print(f"f{n:04d}  (no window)")
            continue
        w, h = r[2] - r[0], r[3] - r[1]
        jump = ""
        if prev is not None:
            d = max(abs(a - b) for a, b in zip(r, prev))
            if d > 40:
                jump = f"  <<JUMP {d}px"
            elif d == 0:
                jump = "  (static)"
        prev = r
        print(f"f{n:04d}  x={r[0]:>5} y={r[1]:>5} w={w:>5} h={h:>5}{jump}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
