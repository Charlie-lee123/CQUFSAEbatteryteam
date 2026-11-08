# -*- coding: utf-8 -*-
r"""实证：最大化/还原覆盖层是否真的会「拉伸变形」。

做法：用 _CoverLabel 的**真实** paintEvent，在多个动画中间几何尺寸下把一张
已知圆形/网格的源图渲染到离屏 QImage，再用像素测量圆是否仍为正圆（w==h）。
若任何一帧的圆被拉成椭圆 ⇒ 真有畸变；否则覆盖层在几何上不可能拉伸。

不需要事件循环 / 不需要桌面，纯离屏渲染，可在沙箱跑。
产物：_check/cover_render/{report.txt}
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QRect, QPoint, QSize  # noqa: E402
from PySide6.QtGui import QImage, QPainter, QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import numpy as np  # noqa: E402


def _make_source(w: int, h: int) -> QPixmap:
    """画一张带圆心圆 + 四角圆 + 网格的源图，用于测宽高比。"""
    pm = QPixmap(w, h)
    pm.fill(QColor(255, 255, 255))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # 背景淡灰网格
    p.setPen(QColor(220, 220, 220))
    step = max(20, w // 40)
    for x in range(0, w, step):
        p.drawLine(x, 0, x, h)
    for y in range(0, h, step):
        p.drawLine(0, y, w, y)
    # 中央正圆（红）
    r = min(w, h) // 4
    cx, cy = w // 2, h // 2
    p.setBrush(QColor(220, 30, 30))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPoint(cx, cy), r, r)
    # 四角小圆（绿）作为额外的宽高比参考
    p.setBrush(QColor(30, 160, 30))
    for (ox, oy) in [(w // 6, h // 6), (5 * w // 6, h // 6),
                     (w // 6, 5 * h // 6), (5 * w // 6, 5 * h // 6)]:
        p.drawEllipse(QPoint(ox, oy), r // 3, r // 3)
    p.end()
    return pm


def _measure_circle(img: QImage) -> tuple[float, float] | None:
    """检测画面里最大的红色连通区域（中央圆）的包围盒宽高。"""
    buf = img.constBits()
    arr = np.frombuffer(bytes(buf), dtype=np.uint8)
    # QImage 默认 Format_RGB32: 每像素 4 字节 BGRA
    stride = img.bytesPerLine()
    w = img.width()
    h = img.height()
    arr = arr.reshape(h, stride // 4, 4)
    r = arr[:, :, 2].astype(np.int32)
    g = arr[:, :, 1].astype(np.int32)
    b = arr[:, :, 0].astype(np.int32)
    mask = (r > 150) & (g < 100) & (b < 100)
    ys, xs = np.where(mask)
    if xs.size < 50:
        return None
    return (float(xs.max() - xs.min() + 1), float(ys.max() - ys.min() + 1))


def main() -> int:
    app = QApplication(sys.argv)
    try:
        from main_window import _CoverLabel
    except Exception as exc:  # noqa: BLE001
        print(f"⛔ 无法导入 _CoverLabel：{exc}")
        return 2

    src_w, src_h = 2000, 1250  # AR = 1.6
    src = _make_source(src_w, src_h)

    # 覆盖层动画会经过的若干几何（AR 从 1.6 渐变到 ~0.78/1.39 等）
    geos = [
        (2000, 1250),  # 起手全屏
        (1700, 1125),
        (1400, 950),
        (1100, 760),
        (1000, 720),   # 终点普通
    ]
    out_dir = os.path.join(_ROOT, "_check", "cover_render")
    os.makedirs(out_dir, exist_ok=True)
    report = []
    max_dev = 0.0
    for (gw, gh) in geos:
        label = _CoverLabel()
        label.set_source(src)
        label.resize(gw, gh)
        img = QImage(gw, gh, QImage.Format.Format_RGB32)
        img.fill(0)
        label.render(img)
        meas = _measure_circle(img)
        if meas is None:
            report.append(f"geo={gw}x{gh} AR={gw/gh:.3f} ⇒ 未检出圆（渲染失败？）")
            continue
        cw, ch = meas
        dev = abs(cw - ch) / max(1.0, min(cw, ch))
        max_dev = max(max_dev, dev)
        report.append(f"geo={gw}x{gh} AR={gw/gh:.3f} ⇒ 圆包围盒 {cw:.1f}x{ch:.1f}"
                      f" 偏差={dev*100:.2f}% {'✅' if dev < 0.01 else '❌畸变'}")
        # 存图供目检
        img.save(os.path.join(out_dir, f"cover_{gw}x{gh}.png"))

    # 同理测 _SettingsShot（起手小矩形→终点大矩形，AR 不同）
    try:
        from main_window import _SettingsShot
        shot = _make_source(560, 700)  # 面板源
        for (gw, gh) in [(40, 40), (200, 260), (560, 700)]:
            s = _SettingsShot(None)
            s._shot = shot
            s._alpha = 1.0
            s.resize(gw, gh)
            img = QImage(gw, gh, QImage.Format.Format_RGB32)
            img.fill(0)
            s.render(img)
            meas = _measure_circle(img)
            if meas is None:
                report.append(f"[SettingsShot] geo={gw}x{gh} ⇒ 未检出圆")
                continue
            cw, ch = meas
            dev = abs(cw - ch) / max(1.0, min(cw, ch))
            max_dev = max(max_dev, dev)
            report.append(f"[SettingsShot] geo={gw}x{gh} AR={gw/gh:.3f}"
                          f" ⇒ 圆包围盒 {cw:.1f}x{ch:.1f} 偏差={dev*100:.2f}%"
                          f" {'✅' if dev < 0.01 else '❌畸变'}")
            img.save(os.path.join(out_dir, f"settings_{gw}x{gh}.png"))
    except Exception as exc:  # noqa: BLE001
        report.append(f"[SettingsShot] 测试跳过：{exc}")

    verdict = "✅ 覆盖层/设置面板全程无畸变（圆始终为正圆）" \
        if max_dev < 0.01 else f"❌ 发现畸变 最大偏差 {max_dev*100:.2f}%"
    lines = ["=== 覆盖层拉伸实证 ===", f"源图 {src_w}x{src_h} AR={src_w/src_h:.3f}",
             ""] + report + ["", verdict]
    txt = "\n".join(lines)
    print(txt)
    with open(os.path.join(out_dir, "report.txt"), "w", encoding="utf-8") as fh:
        fh.write(txt)
    return 0 if max_dev < 0.01 else 1


if __name__ == "__main__":
    sys.exit(main())
