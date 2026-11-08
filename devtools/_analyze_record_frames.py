# -*- coding: utf-8 -*-
"""逐帧分析录屏：找闪烁（亮度/内容突变帧）。"""
import os
import sys

import numpy as np
from PIL import Image

FRAMES = sys.argv[1] if len(sys.argv) > 1 else \
    r"F:\MimoCodeworks\SerialAssistant\_check\anim_record\frames"

# 录屏时间轴：从 0.4s 开始抽帧（60fps 名义帧率，实际 ~15fps）
T0 = 0.4
files = sorted(f for f in os.listdir(FRAMES) if f.endswith(".png"))
imgs = []
for f in files:
    im = Image.open(os.path.join(FRAMES, f)).convert("L")
    a = np.asarray(im, dtype=np.float32)
    imgs.append(a)

print(f"frames={len(imgs)}  size={imgs[0].shape}")
print(f"{'idx':>4} {'t(s)':>6} {'mean':>7} {'diff_prev':>9}  note")

prev = None
results = []
for i, a in enumerate(imgs):
    t = T0 + i * (5.2 / (len(imgs) - 1))
    mean = float(a.mean())
    d = float(np.abs(a - prev).mean()) if prev is not None else 0.0
    results.append((i, t, mean, d))
    prev = a

# 突变帧 = 与前一帧差异显著高于局部基线
diffs = np.array([r[3] for r in results])
base = np.median(diffs[1:])
for i, t, mean, d in results:
    note = ""
    if i > 0 and d > max(3.0, base * 4):
        note = "  <<< 突变"
    if mean < 8:                       # 近黑帧
        note += "  [近黑帧!]"
    print(f"{i:>4} {t:>6.2f} {mean:>7.1f} {d:>9.2f}{note}")
print(f"\n中位帧差(基线)={base:.2f}")
