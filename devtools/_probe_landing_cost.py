import os, sys, time, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtCore import QRectF
import splash_motion as sm

app = QGuiApplication(sys.argv)
W, H, DPR = 1600, 1000, 2.0          # 用户机器：1600x1000@dpr2.0
r = sm.MotionRenderer("dark", DPR, W, H, sm.SPEC_PLANB)
time.sleep(1.5); r._absorb_pending()
pix = r._pixmap(r._clear_w)
s = r._spec

def target_for(t):
    xr, yr, hr, clip, sr, alpha = r._state(t, W / H)
    ink_h = hr * H
    bmp_w = ink_h * s.lockup_aspect / s.ink_w_frac
    bmp_h = bmp_w / s.bitmap_aspect
    left = xr * W - s.ink_left * bmp_w
    top  = yr * H - s.ink_top  * bmp_h
    return QRectF(left, top, bmp_w, bmp_h)

ts = [2.40 + i * 0.80 / 40 for i in range(41)]   # 落点段 0.8s
svg_t, bmp_t = [], []
for t in ts:
    tg = target_for(t)
    # --- SVG 路径（现状）---
    img = QImage(int(W*DPR), int(H*DPR), QImage.Format.Format_RGB32)
    p = QPainter(img); p.scale(DPR, DPR)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    t0 = time.perf_counter()
    if r._svg is not None and r._svg.isValid():
        r._svg.render(p, tg)
    svg_t.append((time.perf_counter()-t0)*1000.0); p.end()
    # --- 位图路径（缓存 lockup 缩放绘制）---
    img2 = QImage(int(W*DPR), int(H*DPR), QImage.Format.Format_RGB32)
    p2 = QPainter(img2); p2.scale(DPR, DPR)
    p2.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    t1 = time.perf_counter()
    p2.drawPixmap(tg, pix, QRectF(pix.rect()))
    bmp_t.append((time.perf_counter()-t1)*1000.0); p2.end()

print(f"SVG   render/frame ms : min={min(svg_t):6.2f} mean={statistics.mean(svg_t):6.2f} max={max(svg_t):6.2f}")
print(f"Bitmap draw /frame ms : min={min(bmp_t):6.2f} mean={statistics.mean(bmp_t):6.2f} max={max(bmp_t):6.2f}")
print(f"SVG   最慢帧等价于 {1000/max(svg_t):5.0f} fps ; Bitmap 最慢帧等价于 {1000/max(bmp_t):5.0f} fps")
print(f"clear_w={r._clear_w} dpr={DPR} 落点目标像素(physical)早期={target_for(2.40).width()*DPR:.0f}px 末期={target_for(3.20).width()*DPR:.0f}px")
