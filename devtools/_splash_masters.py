"""开屏动画 —— 矢量母版切分。

把车队横排中英组合 SVG 拆成「图标 / 中文字 / 英文字」三张矢量母版（浅底+深底），
并输出 layout.json（画布映射、各行像素带、分隔线几何），供 _splash_render.py 逐帧合成。

用法（必须用工具 venv，需 numpy+Pillow；Qt 渲染会转调项目 venv）：
    F:/WorkBuddyTools/venv/Scripts/python.exe devtools/_splash_masters.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DL = os.path.expanduser("~/Downloads")
SRC = os.path.join(DL, "horizontal_bilingual_light_clean.svg")
if not os.path.exists(SRC):
    SRC = os.path.join(ROOT, "assets", "lockup_on_light.svg")

OUT = os.path.join(ROOT, "_check", "splash", "masters")
PY_QT = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
HELPER = os.path.join(ROOT, "devtools", "_svg_to_png.py")

INK_LIGHT = "#122235"   # 品牌墨蓝
INK_DARK = "#FFFFFF"    # 深底反白
RED = "#E60012"         # 闪电红

# —— 设计参数（画布 3840x2160）——
CANVAS_W, CANVAS_H = 3840, 2160
LOCKUP_W = 2765         # 横排版内容目标宽度 = 画布 72%
HERO_W = 1900           # 开场图标主体宽度
MARK_MASTER_W = 4040    # 图标母版（2x 主体宽，逐帧只做缩小）
TEXT_SS = 2             # 文字母版超采样倍数
PAD = 4.0               # 裁切留白（SVG 单位）


def qrender(jobs: list[dict]) -> None:
    """转调项目 venv 的 Qt 渲染器。"""
    if not jobs:
        return
    tmp = os.path.join(OUT, "_jobs.json")
    os.makedirs(OUT, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"jobs": jobs}, fh, ensure_ascii=False)
    res = subprocess.run([PY_QT, HELPER, tmp], capture_output=True, text=True,
                         env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    sys.stdout.write(res.stdout)
    if res.returncode != 0:
        sys.stderr.write(res.stderr)
        raise SystemExit("Qt 渲染失败")


def load_svg(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def split_svg(svg: str) -> tuple[str, str, list[str], str]:
    i = svg.index("<g ")
    j = svg.index("</g>") + 4
    head, gblock, tail = svg[:i], svg[i:j], svg[j:]
    paths = re.findall(r"<path\b[^>]*?/>", tail, re.S)
    rect = re.search(r"<rect\b[^>]*?/>", tail, re.S)
    if not paths or rect is None:
        raise SystemExit("SVG 结构不符合预期")
    return head, gblock, paths, rect.group(0)


def recolor(fragment: str, ink: str) -> str:
    return fragment.replace(INK_LIGHT, ink).replace("#F5F7F8", ink)


def bbox(alpha: np.ndarray, th: int = 16) -> tuple[int, int, int, int]:
    ys, xs = np.where(alpha > th)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def row_runs(alpha: np.ndarray, th: int = 16) -> list[tuple[int, int]]:
    rows = (alpha > th).sum(axis=1)
    runs, start = [], None
    for i, v in enumerate(rows > 0):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(rows) - 1))
    return [r for r in runs if r[1] - r[0] > 3]


def main() -> int:
    svg = load_svg(SRC)
    head, gblock, text_paths, rect = split_svg(svg)
    vb = [float(v) for v in re.search(r'viewBox="([^"]+)"', head).group(1).split()]

    # 1) 2x 探针：量出各块的真实墨迹边界（SVG 坐标 = 像素 / SS）
    ss = 2
    probe_w, probe_h = int(vb[2] * ss), int(vb[3] * ss)
    probe_path = os.path.join(OUT, "_probe_full.png")
    qrender([{ "svg": svg, "out": probe_path, "width": probe_w, "height": probe_h }])
    full = np.array(Image.open(probe_path).convert("RGBA"))[..., 3]

    fx0, fy0, fx1, fy1 = bbox(full)
    fx0, fy0, fx1, fy1 = fx0 / ss, fy0 / ss, fx1 / ss, fy1 / ss
    mx0, my0, mx1, my1 = bbox(full[:, : int(700 * ss)])
    mx0, my0, mx1, my1 = mx0 / ss, my0 / ss, mx1 / ss, my1 / ss
    txt = full[:, int(707 * ss):]
    tx0, ty0, tx1, ty1 = bbox(txt)
    tx0, tx1 = tx0 / ss + 707, tx1 / ss + 707
    ty0, ty1 = ty0 / ss, ty1 / ss
    runs = [(a / ss, b / ss) for a, b in row_runs(txt)]

    print("viewBox      : %s" % vb)
    print("full bbox    : %.1f %.1f %.1f %.1f  (%.1f x %.1f)"
          % (fx0, fy0, fx1, fy1, fx1 - fx0, fy1 - fy0))
    print("mark bbox    : %.1f %.1f %.1f %.1f  (%.1f x %.1f)"
          % (mx0, my0, mx1, my1, mx1 - mx0, my1 - my0))
    print("text bbox    : %.1f %.1f %.1f %.1f  (%.1f x %.1f)"
          % (tx0, ty0, tx1, ty1, tx1 - tx0, ty1 - ty0))
    print("text rows    : %s" % ["%.1f-%.1f" % r for r in runs])

    # 2) 裁切 viewBox
    full_w = fx1 - fx0
    k = LOCKUP_W / full_w
    mark_vb = (mx0 - PAD, my0 - PAD, mx1 - mx0 + 2 * PAD, my1 - my0 + 2 * PAD)
    text_vb = (tx0 - PAD, ty0 - PAD, tx1 - tx0 + 2 * PAD, ty1 - ty0 + 2 * PAD)
    full_vb = (fx0 - PAD, fy0 - PAD, full_w + 2 * PAD, fy1 - fy0 + 2 * PAD)

    text_px_w = int(round(text_vb[2] * k))
    jobs = []
    for ink, tag in ((INK_LIGHT, "light"), (INK_DARK, "dark")):
        body = recolor(gblock, ink)
        mark_svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%f %f %f %f">%s</svg>'
                    % (mark_vb + (body,)))
        height = int(round(MARK_MASTER_W * mark_vb[3] / mark_vb[2]))
        jobs.append({"svg": mark_svg, "out": os.path.join(OUT, "mark_%s.png" % tag),
                     "width": MARK_MASTER_W, "height": height})

        tbody = recolor("".join(text_paths), ink)
        text_svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%f %f %f %f">%s</svg>'
                    % (text_vb + (tbody,)))
        jobs.append({"svg": text_svg, "out": os.path.join(OUT, "text_%s.png" % tag),
                     "width": text_px_w * TEXT_SS,
                     "height": int(round(text_vb[3] * k * TEXT_SS))})

        # 参照帧：整条横排版按最终像素尺寸渲染，用于终帧比对
        fbody = recolor(gblock + "".join(text_paths), ink)
        fbody += recolor(rect, ink) if tag == "light" else rect.replace(INK_LIGHT, "#F5F7F8")
        full_svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%f %f %f %f">%s</svg>'
                    % (full_vb + (fbody,)))
        jobs.append({"svg": full_svg, "out": os.path.join(OUT, "ref_%s.png" % tag),
                     "width": int(round(full_vb[2] * k)),
                     "height": int(round(full_vb[3] * k))})

    qrender(jobs)

    # 3) layout.json
    layout = {
        "canvas": [CANVAS_W, CANVAS_H],
        "k": k,
        "lockup_w": LOCKUP_W,
        "hero_w": HERO_W,
        "full_vb": full_vb,
        "mark_vb": mark_vb,
        "text_vb": text_vb,
        "mark_master_w": MARK_MASTER_W,
        "text_sprite_w": text_px_w,
        "text_rows": [[r[0], r[1]] for r in runs],
        "text_vb_y0": text_vb[1],
        "divider": [702.5, 120.0, 3.0, 128.0],
        "inks": {"light": INK_LIGHT, "dark": INK_DARK, "red": RED},
        "main_dir": OUT,
    }
    with open(os.path.join(OUT, "layout.json"), "w", encoding="utf-8") as fh:
        json.dump(layout, fh, ensure_ascii=False, indent=2)
    print("layout -> %s" % os.path.join(OUT, "layout.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
