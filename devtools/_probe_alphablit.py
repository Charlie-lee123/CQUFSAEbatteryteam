# -*- coding: utf-8 -*-
r"""微基准：**带透明度贴一张全视口大图**到底要多少毫秒。

背景：接收区"旧图淡出"（``main_window._RxShot.paintEvent``）每帧做
``painter.setOpacity(a) + drawPixmap(self.rect(), self._shot)`` ——
``drawPixmap(QRect, QPixmap)`` 是**缩放**重采样（源是 dpr=2 的物理位图，
目标是逻辑矩形），不是 1:1 blit。

这个探针在同一块画布上量三种写法的纯贴图耗时（不含控件/样式/布局）：
  A 现实现：setOpacity + drawPixmap(rect, shot)   —— 缩放 + 透明
  B 同尺寸：setOpacity + drawPixmap(0, 0, shot)   —— dpr 已匹配，1:1 + 透明
  C 不透明：drawPixmap(0, 0, shot)                —— 1:1 且无 alpha

尺寸取真机接收区 viewport（1134 宽窗口、dpr=2 → 972×375 逻辑 / 1944×750 物理）。

用法：.venv\Scripts\python.exe devtools/_probe_alphablit.py
产物：_check/alphablit.txt
"""
from __future__ import annotations

import os
import statistics
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QRect, Qt                                    # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap            # noqa: E402
from PySide6.QtWidgets import QApplication                             # noqa: E402

# 接收区 viewport 的真实尺寸（逻辑 / 物理）
CASES = [(972, 375, 2.0), (972, 375, 1.0), (1600, 620, 2.0)]
N = 25


def make_shot(w: int, h: int, dpr: float) -> QPixmap:
    image = QImage(int(w * dpr), int(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(24, 26, 30))
    painter = QPainter(image)
    painter.setPen(QColor(200, 205, 210))
    for y in range(6, int(h * dpr) - 6, 16):
        painter.drawLine(8, y, int(w * dpr) - 8, y)     # 模拟文字行
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def bench(target: QImage, shot: QPixmap, kind: str, w: int, h: int,
          alpha: float = 0.5) -> list[float]:
    times = []
    for _ in range(N):
        painter = QPainter(target)
        if alpha < 1.0:
            painter.setOpacity(alpha)
        begin = time.perf_counter()
        if kind == "A":
            painter.drawPixmap(QRect(0, 0, w, h), shot)
        else:
            painter.drawPixmap(0, 0, shot)
        times.append((time.perf_counter() - begin) * 1000.0)
        painter.end()
    return times


def main() -> int:
    app = QApplication(sys.argv)                     # noqa: F841 - 需要 GUI 环境
    lines: list[str] = []
    for w, h, dpr in CASES:
        target = QImage(int(w * dpr), int(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        target.setDevicePixelRatio(dpr)
        target.fill(Qt.GlobalColor.transparent)
        shot = make_shot(w, h, dpr)
        line = (f"viewport {w}x{h} @dpr{dpr}  →  物理 {int(w * dpr)}x{int(h * dpr)}"
                f"（{int(w * dpr) * int(h * dpr) / 1e6:.2f} Mpx）")
        print(line)
        lines.append(line)
        table = {}
        for kind, alpha, label in (("A", 0.5, "A 缩放+半透明"),
                                   ("B", 0.5, "B 1:1+半透明"),
                                   ("C", 1.0, "C 1:1 不透明")):
            times = sorted(bench(target, shot, kind, w, h, alpha))
            p50 = statistics.median(times)
            table[kind] = p50
            text = (f"  {label:<14} P50={p50:6.2f}ms  "
                    f"P95={times[int(N * 0.95) - 1]:6.2f}ms  max={times[-1]:6.2f}ms  "
                    f"→ 单帧上限 {1000.0 / p50:6.1f} fps")
            print(text)
            lines.append(text)
        saved = (table["A"] - table["B"]) / table["A"] * 100.0
        text = (f"  缩放重采样占 A 的 {saved:.0f}%；"
                f"B 比 A 快 {table['A'] / table['B']:.2f}×（165Hz 一帧预算 6.06ms）")
        print(text)
        lines.append(text)
        lines.append("")
    out = os.path.join(_ROOT, "_check", "alphablit.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"产物：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
