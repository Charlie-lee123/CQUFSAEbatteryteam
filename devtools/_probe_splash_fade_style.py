# -*- coding: utf-8 -*-
r"""开屏两套收尾的**差异验证**（离屏）：planA 整体高斯模糊淡出 / planB 背景模糊淡出。

用户 2026-11-07：「高斯模糊淡出啊，高斯模糊呢，planA 要整体高斯模糊淡出，
planB 要背景高斯模糊淡出啊」。

判据：
  planA（fade_style="all"）
    A1 logo 区域**锐度单调下降**（模糊在长），k=0.5 的锐度 ≤ k=0 的 75%；
    A2 k=1.0 全透明；
    A3 幕布在 k≤0.35 仍不透明（先糊、后褪，糊的过程看得见）。
  planB（fade_style="bg"）
    B1 logo 在 k≤0.5 保持**清晰**（锐度 ≥ k=0 的 90%）；
    B2 幕布**径向溶解**：同一帧里“四角 alpha”明显低于“中心 alpha”；
    B3 k=1.0 全透明。

锐度 = logo 区域灰度图的平均梯度幅值（模糊会让它掉下来）。
⛔ ``QColor(img.pixel())`` 忽略 alpha，读 alpha 必须 ``qAlpha``。
⛔ 只读源码，不写产品文件、不动 QSettings。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PySide6.QtGui import QGuiApplication, QImage, QPainter, qAlpha

import splash_motion

W, H = 1600, 1000
# planA 无落点：lockup 停在 h1 的位置；planB 有落点。评测窗口都取同一块大区域。
REGION = (0, 0, 900, 420)


def render(m, t: float, fk: float) -> QImage:
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    m.set_fade(fk)
    m.paint(p, W, H, t)
    p.end()
    return img


def _gray(img: QImage, rect) -> np.ndarray:
    x, y, w, h = rect
    sub = img.copy(x, y, w, h).convertToFormat(QImage.Format.Format_Grayscale8)
    bpl = sub.bytesPerLine()
    a = np.frombuffer(sub.constBits(), dtype=np.uint8, count=bpl * sub.height())
    return a.reshape(sub.height(), bpl)[:, :sub.width()].astype(np.int16)


def sharpness(img: QImage) -> float:
    g = _gray(img, REGION)
    gx = np.abs(np.diff(g, axis=1)).mean()
    gy = np.abs(np.diff(g, axis=0)).mean()
    return float(gx + gy)


def main() -> int:
    QGuiApplication.instance() or QGuiApplication([sys.argv[0]])
    ok = True

    def chk(label, cond, extra=""):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}{('  ' + extra) if extra else ''}")
        ok = ok and cond

    print("=== planA：整体高斯模糊淡出 ===")
    a = splash_motion.MotionRenderer("dark", 2.0, W, H, splash_motion.SPEC_PLANA)
    t_hold = splash_motion.SPEC_PLANA.t_total
    s0 = sharpness(render(a, t_hold, 0.0))
    s5 = sharpness(render(a, t_hold, 0.5))
    s9 = sharpness(render(a, t_hold, 0.9))
    print(f"  锐度 k=0 → {s0:.3f}   k=0.5 → {s5:.3f}   k=0.9 → {s9:.3f}")
    chk("A1 logo 越糊越锐度越低", s0 > s5 > s9, f"({s0:.2f} > {s5:.2f} > {s9:.2f})")
    chk("A1b k=0.5 锐度 ≤ 75%", s5 <= s0 * 0.75, f"{s5 / max(1e-6, s0) * 100:.0f}%")
    img0 = render(a, t_hold, 0.30)
    chk("A3 幕布 k=0.30 仍不透明",
        qAlpha(img0.pixel(60, 60)) > 240, f"alpha={qAlpha(img0.pixel(60, 60))}")
    img1 = render(a, t_hold, 1.0)
    max_a = max(qAlpha(img1.pixel(x, y)) for x in range(0, W, 37)
                for y in range(0, H, 37))
    chk("A2 k=1.0 全透明", max_a == 0, f"max={max_a}")

    print("\n=== planB：背景高斯模糊淡出 ===")
    b = splash_motion.MotionRenderer("dark", 2.0, W, H, splash_motion.SPEC_PLANB)
    b.set_land(0.098, 0.078, 0.085)
    t_b = splash_motion.SPEC_PLANB.t_total
    b0 = sharpness(render(b, t_b, 0.0))
    b5 = sharpness(render(b, t_b, 0.5))
    print(f"  锐度 k=0 → {b0:.3f}   k=0.5 → {b5:.3f}")
    chk("B1 logo 保持清晰（≥90%）", b5 >= b0 * 0.90,
        f"{b5 / max(1e-6, b0) * 100:.0f}%")
    mid = render(b, t_b, 0.55)
    corner = min(qAlpha(mid.pixel(x, y)) for x, y in
                 ((4, 4), (W - 5, 4), (4, H - 5), (W - 5, H - 5)))
    center = qAlpha(mid.pixel(W // 2, H // 2))
    chk("B2 幕布径向溶解（四角比中心更透）", corner < center - 8,
        f"corner={corner} center={center}")
    img1b = render(b, t_b, 1.0)
    max_b = max(qAlpha(img1b.pixel(x, y)) for x in range(0, W, 37)
                for y in range(0, H, 37))
    chk("B3 k=1.0 全透明", max_b == 0, f"max={max_b}")

    print("\n结论：" + ("全部 PASS" if ok else "有 FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
