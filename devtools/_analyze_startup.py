# -*- coding: utf-8 -*-
r"""逐帧分析启动录屏：定位「窗口出现 / 开屏海报静止 / 视频开始动」三个转折点。

录屏 10fps，Popen 发生在录屏开始后 PRE_ROLL=1.5s → app 时间 = 帧时间 - 1.5s。
判据：
  · 桌面静止期：相邻帧 diff ≈ 0
  · 窗口首次出现：diff 首次 > 阈值（大面积新内容）
  · 海报静止期：窗口出现后 diff 回落到 ≈ 0 的那一段（= 用户说的"停住"）
  · 视频开始：diff 持续 > 小阈值（视频逐帧在变）
"""
import os
import sys

import numpy as np
from PIL import Image

ROOT = r"F:\MimoCodeworks\SerialAssistant"
TAG = os.environ.get("REC_TAG", "startup")
SRC = os.path.join(ROOT, "_check", "startup_record", TAG + ".mp4")
FR = os.path.join(ROOT, "_check", "startup_record", "frames_" + TAG)
PRE_ROLL = 1.5
FPS = 10.0

if not os.path.isdir(FR) or not os.listdir(FR):
    os.makedirs(FR, exist_ok=True)
    os.system(f'ffmpeg -y -i "{SRC}" -vf scale=400:-1 '
              f'-fps_mode passthrough "{os.path.join(FR, "s%04d.png")}" '
              f'> NUL 2>&1')

files = sorted(f for f in os.listdir(FR) if f.endswith(".png"))
imgs = [np.asarray(Image.open(os.path.join(FR, f)).convert("L"),
                   dtype=np.float32) for f in files]
print(f"frames={len(imgs)}  dur={len(imgs)/FPS:.1f}s  (app t = 帧t - {PRE_ROLL}s)")

rows = []
prev = None
for i, a in enumerate(imgs):
    t_app = (i + 1) / FPS - PRE_ROLL
    m = float(a.mean())
    d = float(np.abs(a - prev).mean()) if prev is not None else 0.0
    rows.append((i, t_app, m, d))
    prev = a

# 阈值：桌面噪声很小（<0.3），窗口出现 >> 2
BIG = 2.0
SMALL = 0.35

first_change = next((r for r in rows if r[3] > BIG), None)
print(f"\n① 首次画面变化（窗口出现）: "
      f"{'t=%.2fs  mean=%.1f diff=%.1f' % (first_change[1], first_change[2], first_change[3]) if first_change else '未检测到'}")

# 从窗口出现之后找「diff 回落到 SMALL 以下」的静止段起点
still_start = None
still_end = None
if first_change:
    idx0 = first_change[0]
    for r in rows[idx0 + 2:]:
        if still_start is None:
            if r[3] < SMALL:
                still_start = r
        else:
            if r[3] > SMALL:
                still_end = r
                break

if still_start:
    print(f"② 海报/窗口静止段开始: t={still_start[1]:.2f}s")
    print(f"   静止段结束（又开始动）: "
          f"{'t=%.2fs' % still_end[1] if still_end else '到录屏结束仍未动'}")
    if still_end:
        print(f"   静止时长 = {still_end[1] - still_start[1]:.2f}s  <<< 用户说的「停住」")
else:
    print("② 没检测到静止段（窗口出现后画面一直在变）")

print(f"\n{'帧':>4} {'t(app)':>8} {'mean':>7} {'diff':>7}")
for i, t, m, d in rows:
    flag = ""
    if first_change and i == first_change[0]:
        flag += " <窗口出现"
    if still_start and i == still_start[0]:
        flag += " <开始静止"
    if still_end and i == still_end[0]:
        flag += " <开始动(视频)"
    print(f"{i:>4} {t:>8.2f} {m:>7.1f} {d:>7.2f}{flag}")
