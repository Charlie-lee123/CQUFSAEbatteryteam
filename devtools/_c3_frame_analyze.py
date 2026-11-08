# -*- coding: utf-8 -*-
"""win-cover-3 录屏逐帧分析：帧间 diff / 重复帧 / 亮度 / 包围盒 + 与遥测对齐。

用法：
  .venv/Scripts/python.exe devtools/_c3_frame_analyze.py --tag c3d
产物：
  _check/_c3_<tag>_frames.csv    逐帧指标（idx, pts, mean, diff, big, bbox）
  stdout 摘要（每个动画窗口一张表）
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "_check"


def load_pts(tag: str) -> list[float]:
    txt = (CHECK / f"_c3_{tag}.ffmpeg.log").read_text(encoding="utf-8",
                                                     errors="replace")
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", txt)]


def decode(tag: str, w: int, h: int):
    """流式解码为灰度帧（w*h），yield (idx, frame)。"""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i",
           str(CHECK / f"_c3_{tag}.mkv"),
           "-vf", f"scale={w}:{h}:flags=area", "-f", "rawvideo",
           "-pix_fmt", "gray", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    n = w * h
    idx = 0
    while True:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        yield idx, np.frombuffer(buf, dtype=np.uint8).reshape(h, w)
        idx += 1
    p.stdout.close()
    p.wait()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="c3d")
    ap.add_argument("--w", type=int, default=800)
    ap.add_argument("--h", type=int, default=500)
    ap.add_argument("--magenta", action="store_true", default=True)
    args = ap.parse_args()

    pts = load_pts(args.tag)
    print(f"pts {len(pts)} 帧")

    rows = []
    prev = None
    for idx, fr in decode(args.tag, args.w, args.h):
        if idx >= len(pts):
            break
        m = float(fr.mean())
        if prev is None:
            d = 0.0
            big = 0
        else:
            df = np.abs(fr.astype(np.int16) - prev.astype(np.int16))
            d = float(df.mean())
            big = int((df > 25).sum())
        # 顶边：从第 0 行往下找第一行"不是洋红背景"的行（洋红灰度≈78）
        rowmean = fr.mean(axis=1)
        hit = np.nonzero(rowmean < 40)[0]
        yrow = int(hit[0]) if hit.size else -1
        rows.append((idx, pts[idx], m, round(d, 3), big, yrow))
        prev = fr

    csv = CHECK / f"_c3_{args.tag}_frames.csv"
    with open(csv, "w", encoding="utf-8") as f:
        f.write("idx,pts,mean,diff,big,yrow\n")
        for r in rows:
            f.write(f"{r[0]},{r[1]:.6f},{r[2]:.3f},{r[3]},{r[4]},{r[5]}\n")
    print(f"写出 {csv.name}（{len(rows)} 行）")

    d = np.array([r[3] for r in rows])
    print(f"diff: p50={np.median(d):.2f} p95={np.percentile(d,95):.2f} "
          f"max={d.max():.2f}")
    # 重复帧（diff≈0）统计
    dup = int((d < 0.02).sum())
    print(f"重复帧（与上一帧几乎全等 diff<0.02）：{dup}/{len(rows)} = "
          f"{100*dup/max(1,len(rows)):.1f}%")
    # 连续重复串长度直方图
    runs, cur = [], 0
    for v in d:
        if v < 0.02:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    import collections
    print("重复串长度直方图:", dict(sorted(collections.Counter(runs).items())))
    # 最大跳变点
    order = np.argsort(d)[::-1][:15]
    print("最大 15 个帧间跳变：")
    for i in sorted(order):
        print(f"  frame {rows[i][0]:5d} pts={rows[i][1]:7.3f} diff={d[i]:7.2f} "
              f"big={rows[i][4]:7d} mean={rows[i][2]:6.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
