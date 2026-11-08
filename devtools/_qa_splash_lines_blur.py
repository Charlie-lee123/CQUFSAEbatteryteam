"""开屏「竖线不提前出现」+「高斯模糊准确」逐像素核验（od47）。

⛔ 必跑场景：od47 把模糊档从 793px 低位图换成 4764px 全分辨率位图后，
   drawPixmap 的边界采样行为与 SVG divider 的实际宽度都变了 —— od45/od46
   调出来的三段 clip 偏移（1.5 / 2.5 / 2.0 逻辑 px）可能失效，导致竖线
   在 t≤1.30 提前漏出。这里用**真实 dpr=2 物理分辨率**逐帧渲染验证。

用法：.venv\\Scripts\\python.exe devtools/_qa_splash_lines_blur.py
（offscreen 即可）
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

W, H, DPR = 1920, 1080, 2.0
BG = np.array([13, 13, 15], dtype=np.int16)   # dark 主题 #0d0d0f


def rgb_of(img: QImage) -> np.ndarray:
    """ARGB32 → HxWx3 (RGB)，按物理像素。"""
    ptr = img.constBits()
    a = np.frombuffer(ptr, dtype=np.uint8,
                      count=img.bytesPerLine() * img.height())
    a = a.reshape(img.height(), img.bytesPerLine() // 4, 4)[:, :img.width(), :]
    # QImage ARGB32 在 little-endian 内存里是 B,G,R,A
    return np.stack([a[:, :, 2], a[:, :, 1], a[:, :, 0]], axis=2).astype(np.int16)


def render(r, t: float) -> np.ndarray:
    img = QImage(int(W * DPR), int(H * DPR), QImage.Format.Format_ARGB32)
    img.setDevicePixelRatio(DPR)
    img.fill(QColor("#0d0d0f"))
    p = QPainter(img)
    r.paint(p, W, H, t)
    p.end()
    return rgb_of(img)


def band(r, t: float):
    """复刻 paint() 里的竖线带几何，返回物理像素 rect（避开左右边界采样）。"""
    s = r._spec
    xr, yr, hr = r._state(t, W / H)[0:3]
    ink_h = hr * H
    bmp_w = ink_h * s.lockup_aspect / s.ink_w_frac
    bmp_h = bmp_w / s.bitmap_aspect
    left = xr * W - s.ink_left * bmp_w
    top = yr * H - s.ink_top * bmp_h
    dx = left + s.divider_x * bmp_w - 1.5
    dy = top + s.divider_y * bmp_h
    dw = s.divider_w * bmp_w + 2.5
    dh = s.divider_h * bmp_h
    # 只取带中间列：左右各让 2 逻辑 px，避开图标/文字 clip 的边界采样伪影
    x0 = int(round((dx + 2.0) * DPR))
    x1 = int(round((dx + max(3.0, dw - 2.0)) * DPR))
    y0 = int(round(dy * DPR))
    y1 = int(round((dy + dh) * DPR))
    return x0, x1, y0, y1, dh * DPR


def main() -> int:
    _app = QApplication(sys.argv)
    import splash_motion as sm

    r = sm.MotionRenderer("dark", DPR, float(W), float(H), sm.SPEC_PLANB)
    s = r._spec
    fails = 0

    print("=" * 74)
    print("[A] 竖线带逐帧墨迹高度（t ≤ t_mark_end 必须恒 0）")
    print("=" * 74)
    # ⛔ 采样点全部由 MotionSpec 推导：od47b 淡入 +0.1s 后整轴顺延，
    #    写死 1.30/1.31 会把「未展开」误判成竖线丢失。
    me = s.t_mark_end
    times = (0.30, 0.55, 0.79, round(s.t_fade_end - 0.01, 4), s.t_fade_end,
             round((s.t_fade_end + me) / 2, 4), round(me - 0.10, 4),
             round(me - 0.01, 4), me,
             round(me + 0.01, 4), round(me + 0.05, 4), round(me + 0.10, 4),
             round(me + 0.15, 4), round(me + s.t_line_ms, 4),
             s.t_text_end, round(s.t_hold_end + 0.40, 4))
    prev_h = 0.0
    for t in times:
        px = render(r, t)
        x0, x1, y0, y1, dh_full = band(r, t)
        x0, x1 = max(0, x0), min(px.shape[1], x1)
        y0, y1 = max(0, y0), min(px.shape[0], y1)
        sub = px[y0:y1, x0:x1]
        if sub.size == 0:
            print(f"  t={t:5.2f}  [FAIL] 竖线带为空矩形")
            fails += 1
            continue
        ink = np.max(np.abs(sub - BG), axis=2) > 12
        rows = np.where(ink.any(axis=1))[0]
        h_px = float(len(rows))
        if t <= s.t_mark_end and h_px > 0:
            print(f"  t={t:5.2f}  竖线墨迹高={h_px:6.1f}px  [FAIL] 提前出现")
            fails += 1
        else:
            tag = "空" if h_px == 0 else f"{h_px:.0f}px"
            print(f"  t={t:5.2f}  竖线墨迹高={tag:>8s}"
                  f"  (满高 {dh_full:.0f}px)  "
                  f"{'OK' if t <= s.t_mark_end else ''}")
        # 落点段（t>t_hold_end）整体缩小，竖线满高本来就变小，不是回退
        if s.t_mark_end < t <= s.t_hold_end and h_px < prev_h - 1.0:
            print(f"  [FAIL] t={t:.2f} 展开高度回退（{prev_h:.0f}→{h_px:.0f}）")
            fails += 1
        prev_h = h_px
    # 展开必须与 ease_out 一致：t_mark_end+0.01 起 >0、+0.20 接近满高
    t_open = round(s.t_mark_end + 0.01, 4)
    px = render(r, t_open)
    x0, x1, y0, y1, dh_full = band(r, t_open)
    sub = px[max(0, y0):min(px.shape[0], y1), max(0, x0):min(px.shape[1], x1)]
    h_open = float((np.max(np.abs(sub - BG), axis=2) > 12).any(axis=1).sum())
    if h_open <= 0:
        print(f"  [FAIL] t={t_open} 竖线未开始展开")
        fails += 1
    print(f"  → t={t_open} 起从中心展开 {h_open:.0f}px，"
          f"t≤{s.t_mark_end} 全程为空"
          if fails == 0 else f"  → {fails} 项 FAIL")

    print()
    print("=" * 74)
    print("[B] 高斯模糊准确性：各档与清晰档的平均像素差（应单调降到 0）")
    print("=" * 74)
    clear = r._pixmap(r._clear_w)
    ci = clear.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    c = rgb_of(ci)
    print(f"  清晰档 {ci.width()}x{ci.height()}  "
          f"(旧实现模糊基图仅 {r._blur_w}px，被拉 "
          f"{ci.width()/r._blur_w:.1f}×)")
    diffs = []
    for st in r._sigma_steps()[::-1]:     # 0.0 → 1.0
        p = r._blur(st)
        if p is None:
            print(f"  [FAIL] _blur({st}) 为 None")
            fails += 1
            continue
        pi = p.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        b = rgb_of(pi)
        d = (float(np.abs(b - c).mean()) if b.shape == c.shape else -1.0)
        diffs.append((st, d, pi.width()))
        print(f"  step={st:5.3f}  sigma_logic={st*s.sigma_screen:5.2f}px  "
              f"与清晰档差={d:7.3f}  尺寸={pi.width()}x{pi.height()}")
    if len(diffs) >= 2:
        mono = all(diffs[i][1] <= diffs[i + 1][1] + 0.15
                   for i in range(len(diffs) - 1))
        near0 = diffs[0][1] < 0.01
        # ⛔ 防「高斯静默失效」：所有档曾恒为同一张图（差 10.911），
        #    单调性也成立，必须额外要求相邻档确有可测差异。
        varied = sum(1 for i in range(len(diffs) - 1)
                     if diffs[i + 1][1] - diffs[i][1] > 0.05)
        print(f"  单调递进={mono}  step=0 与清晰档零差={near0}  "
              f"相邻档有差异的段数={varied}/{len(diffs)-1}")
        if not (mono and near0 and varied >= len(diffs) - 2):
            print("  [FAIL] 模糊档不是真正随 sigma 渐进（疑似降级成同一张图）")
            fails += 1

    print()
    print("[C] 高斯是否曾静默降级")
    err = getattr(sm, "_GAUSSIAN_ERROR", "MISSING")
    print(f"  _GAUSSIAN_ERROR = {err}")
    if err is not None:
        print("  [FAIL] _gaussian 抛异常被吞，模糊未生效")
        fails += 1
    else:
        print("  [PASS] 真高斯执行，无降级")

    print()
    print(f"==== 结论：{'全部通过 ✓' if fails == 0 else str(fails)+' 项 FAIL ✗'} ====")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
