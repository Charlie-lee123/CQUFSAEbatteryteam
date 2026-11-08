# -*- coding: utf-8 -*-
r"""录屏逐帧分析（2026-11-04）：找「闪」的异常帧与「卡」的静止段。

用法：python _analyze_flash_frames.py <mp4> <标签>
把 mp4 按 60fps 抽帧（缩到 800 宽），逐帧算灰度均值 + 相邻帧差分：
  · diff 尖峰 = 内容突跳（候选「闪」帧）
  · 连续 >=3 帧 diff≈0 = 画面静止（运动段里的候选「卡」）
自动保存可疑帧三连图（前/当/后）到 _check/anim_record/dbg_<标签>/，供肉眼核验。
"""
import os
import subprocess
import sys

import cv2  # noqa: E402  (iron rule: imdecode+fromfile)
import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MP4 = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    _ROOT, "_check", "anim_record", "toggle_flash.mp4")
TAG = sys.argv[2] if len(sys.argv) > 2 else "toggle"
FPS = 60.0
OUT = os.path.join(_ROOT, "_check", "anim_record", f"dbg_{TAG}")
W = 800


def imread_u(p):
    return cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-i", MP4, "-vf", f"scale={W}:-2",
                    os.path.join(OUT, "f_%05d.png")],
                   check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    files = sorted(f for f in os.listdir(OUT) if f.endswith(".png"))
    n = len(files)
    print(f"frames={n} ({n / FPS:.2f}s)")
    means, diffs = [], []
    prev = None
    for f in files:
        img = imread_u(os.path.join(OUT, f))
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        means.append(float(g.mean()))
        diffs.append(0.0 if prev is None
                     else float(np.abs(g - prev).mean()))
        prev = g
    diffs = np.array(diffs)
    means = np.array(means)

    order = np.argsort(diffs)[::-1]
    print("\n== diff 尖峰 Top15（候选「闪」帧）==")
    for k in order[:15]:
        i = int(k)
        print(f"  f{i:04d} t={i / FPS:6.3f}s diff={diffs[i]:7.2f} "
              f"mean {means[i - 1]:6.1f}->{means[i]:6.1f}"
              + ("  <<同时下一帧也大跳" if i + 1 < n and diffs[i + 1] > diffs[i] * .6 else ""))

    print("\n== 静止段（diff<0.25 连续>=3 帧，候选「卡」）==")
    run = 0
    for i in range(1, n):
        if diffs[i] < 0.25:
            run += 1
        else:
            if run >= 3:
                print(f"  f{i - run:04d}~f{i - 1:04d} "
                      f"t={((i - run) / FPS):6.3f}~{((i - 1) / FPS):6.3f}s "
                      f"({run} 帧, {run / FPS * 1000:.0f}ms)")
            run = 0

    print("\n== 保存可疑帧三连图（前/当/后）==")
    saved = 0
    seen = set()
    for k in order[:8]:
        i = int(k)
        if any(abs(i - s) < 6 for s in seen):
            continue
        seen.add(i)
        lo, hi = max(1, i - 1), min(n, i + 2)
        imgs = [imread_u(os.path.join(OUT, f"f_{j:05d}.png"))
                for j in range(lo, hi)]
        h = min(im.shape[0] for im in imgs)
        strip = np.hstack([cv2.resize(im, (int(im.shape[1] * h / im.shape[0]), h))
                           for im in imgs])
        cv2.imencode(".png", strip)[1].tofile(
            os.path.join(OUT, f"spike_f{i:04d}.png"))
        saved += 1
    print(f"  saved={saved} -> {OUT}\\spike_f*.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
