"""分析 ddagrab 抓帧：逐帧找「竖直细条」（分隔线候选）与图标包围盒。

用法::

    .venv/Scripts/python.exe devtools/_probe_splash_analyze.py \
        --raw C:/.../runA.raw --band 0,480,2400,1080 \
        --f0 650 --f1 1100 --run 140 --out _check/_splashcap/analyzeA.json

输出（每个抓帧一行）::

    {"i": 812, "fg": 141068, "bbox": [x0,x1,y0,y1],
     "segs": [[x0, x1, maxrun, ytop, ybot], ...]}    # 只列 run>=阈值 的列段

坐标是**物理像素**（抓帧带内），y 需加 band 的 y 偏移才是屏幕物理坐标。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def _max_vrun(mask: np.ndarray) -> np.ndarray:
    """每列的「最长连续亮像素游程」。"""
    h, w = mask.shape
    run = np.zeros(w, dtype=np.int16)
    best = np.zeros(w, dtype=np.int16)
    for y in range(h):
        run += 1
        run *= mask[y]
        np.maximum(best, run, out=best)
    return best


def _segments(cols: np.ndarray, run: np.ndarray) -> list[list[int]]:
    """把命中列合成连续段，返回 [x0, x1, maxrun]。"""
    out: list[list[int]] = []
    if cols.size == 0:
        return out
    start = prev = int(cols[0])
    best = int(run[start])
    for c in cols[1:]:
        c = int(c)
        if c == prev + 1:
            prev = c
            best = max(best, int(run[c]))
            continue
        out.append([start, prev, best])
        start = prev = c
        best = int(run[c])
    out.append([start, prev, best])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--band", default="0,480,2400,1080")
    ap.add_argument("--f0", type=int, default=0)
    ap.add_argument("--f1", type=int, default=-1)
    ap.add_argument("--run", type=int, default=140,
                    help="竖条判定阈值（物理 px 连续亮像素）")
    ap.add_argument("--thresh", type=int, default=60)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    _, _, w, h = (int(v) for v in args.band.split(","))
    raw = np.memmap(args.raw, dtype=np.uint8, mode="r")
    n = raw.size // (w * h)
    fr = raw.reshape(n, h, w)
    f1 = n if args.f1 < 0 else min(n, args.f1)

    rows = []
    for i in range(max(0, args.f0), f1):
        f = fr[i]
        mask = f >= args.thresh
        fg = int(mask.sum())
        rec: dict = {"i": i, "fg": fg}
        if fg:
            xs = np.where(mask.any(axis=0))[0]
            ys = np.where(mask.any(axis=1))[0]
            rec["bbox"] = [int(xs[0]), int(xs[-1]), int(ys[0]), int(ys[-1])]
        else:
            rec["bbox"] = None
        if fg:
            run = _max_vrun(mask)
            cols = np.where(run >= args.run)[0]
            rec["segs"] = _segments(cols, run)
        else:
            rec["segs"] = []
        rows.append(rec)
        if i % 25 == 0:
            print(f"[an] {i}/{f1} fg={fg} segs={rec['segs'][:4]}",
                  file=sys.stderr)

    if args.out:
        Path(args.out).write_text(
            json.dumps({"raw": args.raw, "band": args.band,
                        "frames": n, "run": args.run, "rows": rows},
                       ensure_ascii=False), encoding="utf-8")
        print(f"[an] wrote {args.out} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
