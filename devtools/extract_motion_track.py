"""从开屏设计稿的帧序列提取「原生动画参数表」（离线工具）。

产物 ``assets/splash/planb_motion.json``：每帧一行
``[t, x/屏宽, y/屏高, 墨迹高/屏高, 可见宽度比]``。

为什么要这么干：用户 2026-11-04 的要求是「最终效果要和视频里一样」，但播放端
不许再播视频/帧序列，必须是原生代码绘制。那就把设计稿**量**成参数表 ——
240 行、十几 KB，运行时查表 + 线性插值，绘制仍然是 QPainter 几何绘制。

用法：``.venv/Scripts/python.exe devtools/extract_motion_track.py``
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "splash" / "splash_dark_planb_4k60_frames"
DST = ROOT / "assets" / "splash" / "planb_motion.json"

FPS = 60
FRAMES = 240
W, H = 3200, 2000
LOCKUP_ASPECT = 6.32       # 墨迹宽高比（实测 lockup_on_dark.svg）
THR = 18                   # 灰度阈值：低于它当背景
# 只有 logo 的阶段（模糊段 + 纯 logo 段）：可见宽度占完整 lockup 的比例
LOGO_CLIP = 0.405
# 文字开始擦入的时刻（秒）；从这之后 clip 由实测宽度推
T_TEXT_START = 1.85


def main() -> None:
    rows: list[list[float]] = []
    for i in range(1, FRAMES + 1):
        t = (i - 1) / FPS
        g = cv2.imread(str(SRC / f"frame_{i:05d}.jpg"), cv2.IMREAD_GRAYSCALE)
        if g is None:
            continue
        mask = g > THR
        ys, xs = np.where(mask)
        if len(xs) == 0:
            rows.append([round(t, 4), 0.0, 0.0, 0.0, LOGO_CLIP])
            continue
        x0, y0 = int(xs.min()), int(ys.min())
        bw = int(xs.max() - x0 + 1)
        bh = int(ys.max() - y0 + 1)
        # 由 bbox 反推「露出比例」：清晰时 bw = bh × 6.32 × clip
        clip = bw / (bh * LOCKUP_ASPECT) if bh > 0 else LOGO_CLIP
        if t < T_TEXT_START:
            # 模糊段 bbox 被高斯撑大（bw/bh 比例失真），这一段锁死 logo 比例；
            # 尺寸也受撑大影响，交给渲染端的模糊曲线去补。
            clip = LOGO_CLIP
        else:
            clip = max(0.40, min(1.02, clip))
        rows.append([round(t, 4), round(x0 / W, 5), round(y0 / H, 5),
                     round(bh / H, 5), round(clip, 4)])

    DST.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    print(f"rows={len(rows)} bytes={DST.stat().st_size}")
    for r in rows[::20]:
        print("  %.2fs  x=%.4f y=%.4f h=%.4f clip=%.3f" % (r[0], r[1], r[2], r[3], r[4]))


if __name__ == "__main__":
    main()
