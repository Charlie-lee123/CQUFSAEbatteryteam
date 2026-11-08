# -*- coding: utf-8 -*-
r"""开屏收尾时序探针（离屏，不开真实窗口）：验证「落点全程不褪幕 → 到位后才淡出」。

判据（用户 2026-11-06：「到位后再淡出啊」）
------------------------------------------
A. 落点段（t_hold_end … t_land_end）整屏底色 alpha 恒 = 255
   —— logo 飞行途中幕布不许化开（od51 让它随 _ease_land 一起收口，被用户否掉）。
B. 淡出段（落定后才启动，_FADE_MS=300）：底色 255 → 0 单调下降、k≥0.8 基本褪尽；
   同时 logo 在 k≤0.5 仍是实心（先让主窗显出来，logo 最后才溶掉）。
C. 落定位置 = 注入落点（≤0.5 逻辑像素），淡出 k=1 全透明。

⛔ 坑：``QColor(img.pixel(x,y))`` 会**忽略 alpha**（Qt 的 QColor(QRgb) 语义），
   必须用 ``qAlpha(img.pixel(...))`` —— 第一版就栽在这，读出来全是 255。
⛔ 只读源码、不写产品文件、不动 QSettings。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QGuiApplication, QImage, QPainter, qAlpha

import splash_motion

W, H = 1600, 1000
LAND = (30.0, 78.0, 40.0)          # 本机实测口径：x=30 y=78 h=40 逻辑像素


def render(m, t: float, fk: float = 0.0) -> QImage:
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    m.set_fade(fk)
    m.paint(p, W, H, t)
    p.end()
    return img


def a_at(img: QImage, x: int, y: int) -> int:
    return qAlpha(img.pixel(x, y))


def logo_max_alpha(img: QImage) -> int:
    """落点墨迹盒内的最大 alpha（logo 是否实心）。"""
    x0, y0, h = int(LAND[0]), int(LAND[1]), int(LAND[2])
    return max(a_at(img, x, y)
               for x in range(max(0, x0), min(W, x0 + 220), 2)
               for y in range(max(0, y0), min(H, y0 + h), 2))


def main() -> int:
    # ⛔ 必须先有 QGuiApplication：MotionRenderer 构造要建 QPixmap，没有 app
    #    实例时 Qt 直接 qFatal 退出（实测 exit 127、无任何输出）。
    QGuiApplication.instance() or QGuiApplication([sys.argv[0]])
    spec = splash_motion.SPEC_PLANB
    m = splash_motion.MotionRenderer("dark", 2.0, W, H, spec)
    m.set_land(LAND[0] / W, LAND[1] / H, LAND[2] / H)

    print(f"spec: t_hold_end={spec.t_hold_end}  t_land_end={spec.t_land_end}"
          f"  t_total={spec.t_total}  淡出另计 300ms")
    print(f"画布 {W}x{H}  底色 #0d0d0f  注入落点 x={LAND[0]} y={LAND[1]} h={LAND[2]}\n")

    ok = True
    # ---- A. 落点段底色恒不透明
    print("== A. 落点段（飞行途中）底色 ==")
    for t in (spec.t_hold_end, spec.t_hold_end + 0.2,
              spec.t_hold_end + 0.45, spec.t_land_end - 0.05, spec.t_land_end):
        a = a_at(render(m, t), 8, 8)
        good = a == 255
        ok = ok and good
        print(f"  t={t:5.2f}s  底色 alpha={a:3d}  [{'OK' if good else 'FAIL'}]")

    # ---- B. 淡出段
    print("\n== B. 淡出段（落定之后）底色 / logo ==")
    prev = 256
    for fk in (0.0, 0.2, 0.4, 0.55, 0.7, 0.85, 1.0):
        img = render(m, spec.t_land_end, fk)
        bg = a_at(img, 8, 8)
        lg = logo_max_alpha(img)
        mono = "OK" if bg <= prev else "FAIL(非单调)"
        if bg > prev:
            ok = False
        prev = bg
        print(f"  fade k={fk:4.2f}  底色 alpha={bg:3d}  落点 logo 最大 alpha={lg:3d}"
              f"  [{mono}]")

    img0 = render(m, spec.t_land_end, 0.0)
    img5 = render(m, spec.t_land_end, 0.5)
    img85 = render(m, spec.t_land_end, 0.85)
    img1 = render(m, spec.t_land_end, 1.0)
    checks = [
        (f"k=0.0 底色=255（落定瞬间幕布还在）", a_at(img0, 8, 8) == 255),
        (f"k=0.0 落点 logo 实心(≥250)", logo_max_alpha(img0) >= 250),
        (f"k=0.5 logo 仍实心(≥250)", logo_max_alpha(img5) >= 250),
        (f"k=0.85 底色≈0(≤4)", a_at(img85, 8, 8) <= 4),
        (f"k=1.0 全透明（撤窗不可见）", a_at(img1, 8, 8) == 0
         and logo_max_alpha(img1) == 0),
    ]
    print()
    for name, good in checks:
        print(f"  [{'PASS' if good else 'FAIL'}] {name}")
        ok = ok and good

    # ---- C. 落定位置
    xr, yr, hr, clip, sig, al = m._state(spec.t_land_end, W / H)
    d = max(abs(xr * W - LAND[0]), abs(yr * H - LAND[1]), abs(hr * H - LAND[2]))
    print(f"\n== C. 落定 _state: x={xr * W:.2f} y={yr * H:.2f} h={hr * H:.2f}"
          f" clip={clip:.3f} sigma={sig:.3f} alpha={al:.3f}")
    print(f"  与注入落点最大偏差 = {d:.3f} 逻辑像素  "
          f"[{'PASS' if d <= 0.5 else 'FAIL'}]")
    ok = ok and d <= 0.5

    print("\n结论：" + ("全部 PASS" if ok else "存在 FAIL，见上"))
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    # ⛔ Qt 析构偶发 segfault（exit 139），用 os._exit 直接交回码，别让解释器
    #    在 teardown 里把结论吞掉。
    sys.stdout.flush()
    os._exit(code)
