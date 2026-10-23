# -*- coding: utf-8 -*-
r"""rx_strip 探针的离线像素取证：读 `_check/rx_strip_*_full.png`，不开窗口。

产出：
  1. ASCII 结构图 —— 把"偏离卡片底色"的像素画成字符画，直接看条子长什么样；
  2. 彩色像素聚类 —— 全 UI 只有开关是蓝的，蓝色像素的位置 = 开关旋钮的位置；
  3. 关键行的原始 RGB。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(_ROOT, "_check")

from PySide6.QtGui import QImage                          # noqa: E402

RX = 72          # opts_row / txt_rx 左沿（物理）
BAND_Y0 = 150    # 分析起点
BAND_Y1 = 280


def load(name: str, kind: str = "full") -> QImage:
    img = QImage(os.path.join(CHECK, f"rx_strip_{name}_{kind}.png"))
    return img


def diff_rows(a: QImage, b: QImage, tag: str, y0: int = 150, y1: int = 280,
              x0: int = 80, x1: int = 2600) -> list[str]:
    """两张同尺寸抓图逐行平均差（PrintWindow vs 屏幕 —— 若屏幕有而 pw 无，即陈旧像素）。"""
    out = [f"== {tag} pw vs screen 逐行平均通道差 =="]
    hits = []
    for y in range(y0, min(y1, a.height(), b.height())):
        total = 0
        n = 0
        for x in range(x0, x1, 8):
            ca, cb = a.pixelColor(x, y), b.pixelColor(x, y)
            total += (abs(ca.red() - cb.red()) + abs(ca.green() - cb.green())
                      + abs(ca.blue() - cb.blue()))
            n += 1
        d = total / max(1, n * 3)
        if d > 1.0:
            hits.append((y, d))
    if not hits:
        out.append("    （逐行全一致，差 <1/通道）")
    for y, d in hits[:40]:
        out.append(f"    y={y} 平均差={d:.1f}")
    return out


def bg_color(img: QImage, y: int) -> tuple[int, int, int]:
    """取该行 x∈[1100..1300]（工具栏内容之外的空档）的中位色当底色。"""
    import statistics
    rs, gs, bs = [], [], []
    for x in range(1100, 1300, 4):
        c = img.pixelColor(x, y)
        rs.append(c.red())
        gs.append(c.green())
        bs.append(c.blue())
    return (int(statistics.median(rs)), int(statistics.median(gs)),
            int(statistics.median(bs)))


def ascii_map(img: QImage, tag: str, x0: int = RX, x1: int = 1200,
              y0: int = BAND_Y0, y1: int = BAND_Y1) -> list[str]:
    out = [f"== {tag} 结构图（每字符=4物理px，行=物理行 {y0}..{y1}； "
           "'#'=强偏离 '@'=中 '.'=弱 ' '=背景）=="]
    for y in range(y0, y1, 2):                      # 隔一行，压扁一点
        bg = bg_color(img, y)
        row = []
        for x in range(x0, x1, 4):
            c = img.pixelColor(x, y)
            d = abs(c.red() - bg[0]) + abs(c.green() - bg[1]) + abs(c.blue() - bg[2])
            row.append("#" if d > 120 else "@" if d > 60 else "." if d > 24 else " ")
        out.append(f"{y:4d} " + "".join(row))
    return out


def colored_clusters(img: QImage, tag: str, y0: int = 140, y1: int = 400,
                     x0: int = 0, x1: int = 3200) -> list[str]:
    """找彩色（蓝紫）像素并按行聚合，报告每簇的 (y范围, x范围, 像素数)。"""
    pts = []
    for y in range(y0, y1):
        for x in range(x0, x1, 2):
            c = img.pixelColor(x, y)
            if c.blue() - max(c.red(), c.green()) > 24:      # 蓝/紫
                pts.append((x, y))
    out = [f"== {tag} 彩色像素（蓝紫，b-max(r,g)>24）共 {len(pts)} 点 =="]
    if not pts:
        out.append("    （无）")
        return out
    # 按 y 聚成带
    pts.sort(key=lambda p: p[1])
    bands = []
    cur = [pts[0]]
    for p in pts[1:]:
        if p[1] - cur[-1][1] <= 3:
            cur.append(p)
        else:
            bands.append(cur)
            cur = [p]
    bands.append(cur)
    for b in bands:
        ys = [p[1] for p in b]
        xs = [p[0] for p in b]
        out.append(f"    y {min(ys)}..{max(ys)}  x {min(xs)}..{max(xs)}  "
                   f"n={len(b)}  （逻辑 y {min(ys) // 2}..{max(ys) // 2}）")
    return out


def raw_rows(img: QImage, tag: str, rows: list[int], xs: list[int]) -> list[str]:
    out = [f"== {tag} 原始 RGB（行: x=值…）=="]
    for y in rows:
        bg = bg_color(img, y)
        cells = []
        for x in xs:
            c = img.pixelColor(x, y)
            d = abs(c.red() - bg[0]) + abs(c.green() - bg[1]) + abs(c.blue() - bg[2])
            cells.append(f"x{x}:({c.red()},{c.green()},{c.blue()},{d})")
        out.append(f"  y={y} bg={bg}  " + "  ".join(cells))
    return out


def main() -> int:
    lines: list[str] = []
    tags = sorted({f[len("rx_strip_"):-len("_full.png")]
                   for f in os.listdir(CHECK)
                   if f.startswith("rx_strip_") and f.endswith("_full.png")})
    lines.append(f"场景：{tags}")
    for tag in tags:
        img = load(tag)
        if img.isNull():
            continue
        lines += ascii_map(img, tag)
        lines += colored_clusters(img, tag)
        scr = load(tag, "screen")
        if not scr.isNull() and scr.size() == img.size():
            lines += diff_rows(img, scr, tag)
    # 关键行原始值：S1 的条子区（172..181）与干净区（154..170）对照
    s1 = load("S1")
    if not s1.isNull():
        lines += raw_rows(s1, "S1(最大化,隐藏)", [156, 160, 172, 174, 176, 178, 180,
                                                  182, 204, 206, 208],
                          [80, 120, 200, 300, 500, 800, 1100])
    s7 = load("S7")
    if not s7.isNull():
        lines += raw_rows(s7, "S7(最大化,工具栏可见)", [156, 160, 172, 176, 180,
                                                       182, 186, 204, 206],
                          [80, 120, 200, 300, 500, 800, 1100])
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(CHECK, "rx_strip_forensic.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
