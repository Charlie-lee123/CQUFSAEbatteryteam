"""按坐标裁一小块并放大：python devtools/_crop_zoom.py in.png x y w h scale out.png"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage


def main() -> int:
    if len(sys.argv) < 8:
        print(__doc__)
        return 2
    src, x, y, w, h, scale, out = sys.argv[1:8]
    img = QImage(src)
    if img.isNull():
        print("load failed")
        return 1
    x, y, w, h, scale = int(x), int(y), int(w), int(h), int(scale)
    for name, (cx, cy) in {"L": (x, y)}.items():
        _ = name, cx, cy
    crop = img.copy(x, y, min(w, img.width() - x), min(h, img.height() - y))
    big = crop.scaled(crop.width() * scale, crop.height() * scale,
                      Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.FastTransformation)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    big.save(out)
    print("wrote", out, crop.width(), crop.height(), "->", big.width(), big.height())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
