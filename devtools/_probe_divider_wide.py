"""竖线「提前出现」的宽窗复核（只读，不改产品代码）。

`_probe_divider_real.py analyze` 的分隔线检测窗是 [div_x-16, div_x+16]。
本脚本把窗口放宽到 ±40 phys px，并**逐帧报告命中列**，用来排除
「时间对齐偏 20~40px 导致窗口没套住残影列」这一种漏判。

用法：
    python devtools/_probe_divider_wide.py --tag dark_on --A -1.4575
    python devtools/_probe_divider_wide.py --tag light_on --A -1.3708
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "devtools"))

import _probe_divider_real as P  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--A", type=float, required=True)
    ap.add_argument("--half", type=int, default=40)
    ap.add_argument("--tol", type=int, default=2)
    args = ap.parse_args()

    rows, boot, land = P._load_trace(args.tag)
    band = tuple(boot["band"])
    wlog, hlog = int(boot["wlog"]), int(boot["hlog"])
    dpr = float(boot["dpr_renderer"] or boot["dpr_win"])
    scheme = boot["scheme"]
    fps = 120.0
    t_mark = float(boot["spec"]["t_mark_end"])

    lt = land["land"] if land else [0.03, 0.0785, 0.085]
    table, _ = P._offscreen_table(wlog, hlog, dpr, band, lt, scheme, args.tol)
    tt = np.array([r["t"] for r in table])
    t_divx = np.array([r["div_x"] for r in table])
    t_iconh = np.array([r["div_h"] for r in table])

    mm, n = P._video_frames(P.OUTDIR / f"{args.tag}.raw", band[2], band[3])
    x0 = band[0]
    bw, bh = band[2], band[3]

    print(f"[wide] {args.tag} band={band} half=±{args.half} tol={args.tol} "
          f"frames={n}")
    worst = []
    for i in range(n):
        mt = args.A + i / fps
        if not (-0.02 <= mt < t_mark - 0.004):
            continue
        f = np.asarray(mm[i])
        bg = int(np.bincount(f.ravel(), minlength=256).argmax())
        mask = np.abs(f.astype(np.int16) - bg) > args.tol
        run = P._max_vrun(mask)
        divx = float(np.interp(mt, tt, t_divx))
        lo = int(round(divx - x0))
        a, b = max(0, lo - args.half), min(bw, lo + args.half + 1)
        if b <= a:
            continue
        seg = run[a:b]
        k = int(np.argmax(seg))
        h = float(np.interp(mt, tt, t_iconh))
        worst.append((int(seg.max()), a + k + x0, round(mt, 3), round(divx, 1),
                      round(h, 1)))

    worst.sort(reverse=True)
    # 只统计「开屏已经在屏上」的帧（mt>=0.10）：更早的帧是桌面/上一轮窗口，
    # 不属于开屏内容，混进来会把 max 抬高（实测刚好在 x=2577 有一根 24px 的桌面竖线）。
    live = [w for w in worst if w[2] >= 0.10]
    if live:
        lv = [w[0] for w in live]
        print(f"[wide] ★ 开屏在屏帧（mt>=0.10，n={len(live)}）："
              f"run max={max(lv)} P95={sorted(lv)[int(len(lv)*.95)]} "
              f"P50={sorted(lv)[len(lv)//2]}")
        print(f"[wide] ★ 其中 run>=3 的帧数 = {sum(1 for v in lv if v >= 3)}，"
              f">=8 的 {sum(1 for v in lv if v >= 8)}，"
              f">=30 的 {sum(1 for v in lv if v >= 30)}")
        for w in live[:5]:
            print(f"   live {w[0]:4d} px x={w[1]:5d} mt={w[2]:5.3f} "
                  f"div_x={w[3]:7.1f} 偏移={w[1]-w[3]:+.1f}")
    print(f"[wide] 预竖线段帧数 {len(worst)}（mt<{t_mark}）")
    print("[wide] 游程最大的 12 帧：run / 命中列x / mt / div_x / 该帧分隔线高")
    for w in worst[:12]:
        print(f"   {w[0]:4d} px  x={w[1]:5d}  mt={w[2]:5.3f}  div_x={w[3]:7.1f}  "
              f"div_h={w[4]:5.1f}  偏移={w[1]-w[3]:+.1f}")
    vals = [w[0] for w in worst]
    print(f"[wide] run 分布：max={max(vals)} P95={sorted(vals)[int(len(vals)*.95)]} "
          f"P50={sorted(vals)[len(vals)//2]} n={len(vals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
