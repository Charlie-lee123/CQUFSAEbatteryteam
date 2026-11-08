"""高斯淡入的预生成代价实测（只读，不改产品源码）。

对「模组行区域」这一块（6 行 = 473×264 逻辑，dpr2 = 946×528 物理）测三种做法：
  A) splash_motion._gaussian 直接对全分辨率 QPixmap 做（项目内唯一"高质量"实现）
  B) 先缩到 1/k，在低分辨率上做 sigma/k，再放大（视觉等价，代价降一个数量级）
  C) 逐档预热总耗时（6 档 / 8 档）

    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_probe_vehicle_blur.py
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])            # noqa: F841
    import splash_motion

    W, H = 946, 528                             # 6 行区域（物理像素）

    def sample_image() -> QImage:
        img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(24, 24, 27))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for i in range(6):
            y = 8 + i * 92
            p.setPen(QColor(220, 220, 225))
            p.drawText(28, y + 30, f"Module {i + 1}")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(70, 70, 78))
            p.drawRoundedRect(300, y + 6, 250, 44, 8, 8)
            p.drawRoundedRect(620, y + 6, 250, 44, 8, 8)
        p.end()
        return img

    img = sample_image()
    pix = QPixmap.fromImage(img)
    reps = 3

    def bench(fn) -> dict:
        vals = []
        for _ in range(reps):
            import time
            t0 = time.perf_counter()
            fn()
            vals.append((time.perf_counter() - t0) * 1000.0)
        return {"p50": round(statistics.median(vals), 2),
                "min": round(min(vals), 2), "max": round(max(vals), 2)}

    print(f"[blur] 原图 {W}x{H} phys")
    splash_motion._GAUSSIAN_ERROR = None
    rows = []
    for sigma in (18.0, 12.0, 8.0, 4.0, 2.0):
        a = bench(lambda s=sigma: splash_motion._gaussian(pix, s))
        rows.append(("A 全分辨率", sigma, a))
        for k in (2, 4, 8):
            sw, sh = W // k, H // k
            small = pix.scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)

            def run(s=sigma, k=k, small=small):
                b = splash_motion._gaussian(small, s / k)
                b.scaled(W, H, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)

            rows.append((f"B 1/{k}", sigma, bench(run)))
    print(f"[blur] _GAUSSIAN_ERROR = {splash_motion._GAUSSIAN_ERROR!r}")
    print(f"{'方案':<12}{'sigma':>7}{'p50(ms)':>10}{'min':>8}{'max':>8}")
    for name, sigma, r in rows:
        print(f"{name:<12}{sigma:>7.1f}{r['p50']:>10.2f}{r['min']:>8.2f}{r['max']:>8.2f}")

    # 6 档预生成总耗时（sigma 18→0 线性，取 B 1/4 与 B 1/8）
    for k in (4, 8):
        sel = [r for n, s, r in rows if n == f"B 1/{k}"]
        print(f"[blur] B 1/{k}: 5 档合计 {sum(x['p50'] for x in sel):.1f}ms，"
              f"6 档外推 {sum(x['p50'] for x in sel) * 6 / 5:.1f}ms")
    sel_a = [r for n, s, r in rows if n == "A 全分辨率"]
    print(f"[blur] A 全分辨率: 5 档合计 {sum(x['p50'] for x in sel_a):.1f}ms，"
          f"6 档外推 {sum(x['p50'] for x in sel_a) * 6 / 5:.1f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
