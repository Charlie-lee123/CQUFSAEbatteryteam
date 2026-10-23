"""把用户截图里的「接收区顶部小条」放大，并逐行统计内容，定位它的真实像素范围。

用法：python devtools/_zoom_strip.py <png> [顶部搜索范围]
输出：_check/strip_zoom.png（条带 8 倍放大）、_check/strip_ctx.png（上下文 4 倍）
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_check"


def row_stats(img: QImage) -> list[tuple[int, int, int, int]]:
    """每行：(非背景像素数, 最亮, 最暗, 蓝色像素数)。背景取该行最左 6 列的中位色。"""
    w, h = img.width(), img.height()
    out = []
    for y in range(h):
        bg = img.pixelColor(2, y)
        bg_l = bg.lightness()
        cnt = 0
        hi = 0
        lo = 255
        blue = 0
        for x in range(w):
            c = img.pixelColor(x, y)
            d = abs(c.lightness() - bg_l)
            cnt += d > 8
            hi = max(hi, c.lightness())
            lo = min(lo, c.lightness())
            if c.blue() - c.red() > 26 and c.blue() > 90:
                blue += 1
        out.append((cnt, hi, lo, blue))
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("need png path")
        return 2
    src = Path(sys.argv[1])
    img = QImage(str(src))
    if img.isNull():
        print("load failed", src)
        return 1
    print(f"image {img.width()}x{img.height()}")
    stats = row_stats(img)
    print("y   cnt   hi   lo  blue")
    for y, (cnt, hi, lo, blue) in enumerate(stats):
        if cnt or blue:
            print(f"{y:3d} {cnt:5d} {hi:4d} {lo:4d} {blue:5d}")

    OUT.mkdir(exist_ok=True)
    # 找"内容稀少但确实有内容"的窄带：cnt 介于 1 和宽 30% 之间
    w = img.width()
    band = [y for y, (cnt, _hi, _lo, _blue) in enumerate(stats)
            if 0 < cnt < max(8, int(w * 0.45))]
    print("candidate thin rows:", band[:40])
    if band:
        y0 = max(0, band[0] - 10)
        y1 = min(img.height(), band[-1] + 10)
        crop = img.copy(0, y0, w, max(1, y1 - y0))
        big = crop.scaled(crop.width() * 4, crop.height() * 4,
                          Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.FastTransformation)
        big.save(str(OUT / "strip_ctx.png"))
        print("wrote", OUT / "strip_ctx.png", crop.width(), crop.height())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
