"""落点速率曲线对比：对称 smoothstep vs 非对称临界阻尼型 glide(Beta(2,3) CDF)。"""

import os
from PIL import Image, ImageDraw, ImageFont


def smoothstep(x):
    return x * x * (3.0 - 2.0 * x)  # 3x²-2x³


def glide(x):
    return x * x * (6.0 - 8.0 * x + 3.0 * x * x)  # 6x²-8x³+3x⁴  Beta(2,3) CDF


def d(f, x, h=1e-6):
    return (f(min(1, x + h)) - f(max(0, x - h))) / (min(1, x + h) - max(0, x - h))


print("=== 端点与导数（解析/数值）===")
for name, f in (("smoothstep", smoothstep), ("glide", glide)):
    print(
        f"{name:11s} f(0)={f(0):.6f} f(1)={f(1):.6f} "
        f"f'(0)={d(f, 0):+.4f} f'(1)={d(f, 1):+.4f} "
        f"f''(1)~={(f(1) - 2 * f(0.999) + f(0.998)) / 1e-6:.1f}"
    )
    # 峰值速度位置
    xs = [i / 10000 for i in range(10001)]
    pk = max(xs, key=lambda x: d(f, x))
    print(f"{'':11s} 峰值速度 {d(f, pk):.4f} @ x={pk:.4f}")
    seg = [round(100 * (f((i + 1) / 10) - f(i / 10)), 2) for i in range(10)]
    print(f"{'':11s} 每 0.1 位移% = {seg}")
    mono = all(f(b) >= f(a) - 1e-12 for a, b in zip(xs, xs[1:]))
    print(
        f"{'':11s} 单调无过冲: {mono}; 全程 f∈[0,1]: {all(0 <= f(x) <= 1 for x in xs)}"
    )

# ---- 对比图 ----
W, H = 1360, 1060
PAD = 70
img = Image.new("RGB", (W, H), (16, 16, 19))
dr = ImageDraw.Draw(img)
try:
    fnt = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 20)
    fsm = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 16)
    fbig = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 26)
except Exception:
    fnt = fsm = fbig = ImageFont.load_default()
GX0, GY0, GX1, GY1 = PAD, 90, 650, 470  # 位移图
VX0, VY0, VX1, VY1 = 710, 90, 1300, 470  # 速度图


def frame(x0, y0, x1, y1, title):
    dr.rectangle((x0, y0, x1, y1), outline=(60, 60, 68), width=1)
    dr.text((x0, y0 - 34), title, font=fbig, fill=(235, 235, 240))
    for i in range(6):
        gy = y0 + (y1 - y0) * i / 5
        dr.line((x0, gy, x1, gy), fill=(38, 38, 44))
        dr.text((x0 - 38, gy - 10), f"{1 - i / 5:.1f}", font=fsm, fill=(120, 120, 130))
        gx = x0 + (x1 - x0) * i / 5
        dr.line((gx, y0, gx, y1), fill=(38, 38, 44))
        dr.text((gx - 8, y1 + 8), f"{i / 5:.1f}", font=fsm, fill=(120, 120, 130))


frame(GX0, GY0, GX1, GY1, "Displacement  f(x)")
frame(VX0, VY0, VX1, VY1, "Velocity  f'(x)  (normalized)")


def plot(f, x0, y0, x1, y1, color, width=4, velocity=False, norm=None):
    pts = []
    for i in range(501):
        x = i / 500
        v = (d(f, x) / norm) if velocity else f(x)
        pts.append((x0 + (x1 - x0) * x, y1 - (y1 - y0) * max(0, min(1, v))))
    dr.line(pts, fill=color, width=width, joint="curve")


OLD = (120, 130, 150)
NEW = (90, 190, 255)
plot(smoothstep, GX0, GY0, GX1, GY1, OLD)
plot(glide, GX0, GY0, GX1, GY1, NEW)
plot(smoothstep, VX0, VY0, VX1, VY1, OLD, velocity=True, norm=1.5)
plot(glide, VX0, VY0, VX1, VY1, NEW, velocity=True, norm=16 / 9)
# 峰值竖线
for x, col, lab in ((0.5, OLD, "peak 0.50"), (1 / 3, NEW, "peak 0.33")):
    gx = VX0 + (VX1 - VX0) * x
    dr.line((gx, VY1, gx, VY1 - 70), fill=col, width=2)
    dr.text((gx - 34, VY1 - 96), lab, font=fsm, fill=col)
# 图例 + 阶段标注
dr.line((GX0 + 20, GY1 + 58, GX0 + 60, GY1 + 58), fill=OLD, width=4)
dr.text((GX0 + 68, GY1 + 46), "OLD: symmetric smoothstep", font=fnt, fill=OLD)
dr.line((GX0 + 430, GY1 + 58, GX0 + 470, GY1 + 58), fill=NEW, width=4)
dr.text((GX0 + 478, GY1 + 46), "NEW: glide", font=fnt, fill=NEW)
for x, lab, col in (
    (0.08, "SLOW start", (150, 200, 160)),
    (0.33, "FAST", (255, 200, 110)),
    (0.80, "LONG DECEL tail", (160, 190, 255)),
):
    gx = VX0 + (VX1 - VX0) * x
    dr.text((gx - 40, VY0 + 8), lab, font=fsm, fill=col)
# 每 0.1 位移表
y = 560
dr.text(
    (PAD, y),
    "Progress per 0.1 of landing time ( % of total travel )",
    font=fbig,
    fill=(235, 235, 240),
)
y += 44
for name, f, col in (
    ("OLD smoothstep", smoothstep, OLD),
    ("NEW glide     ", glide, NEW),
):
    seg = [100 * (f((i + 1) / 10) - f(i / 10)) for i in range(10)]
    dr.text((PAD, y), name, font=fnt, fill=col)
    bx = PAD + 210
    bw = 100
    for i, v in enumerate(seg):
        h = int(v / 18 * 150)
        dr.rectangle(
            (bx + i * (bw + 6), y + 170 - h, bx + i * (bw + 6) + bw, y + 170), fill=col
        )
        dr.text(
            (bx + i * (bw + 6) + 24, y + 176),
            f"{v:.1f}",
            font=fsm,
            fill=(180, 180, 190),
        )
    y += 230
dr.text(
    (PAD, 1030),
    "glide = 6x^2 - 8x^3 + 3x^4  |  f'(0)=f'(1)=0, f''(1)=0 (C2 settle), peak @ x=1/3, no overshoot, f(1)=1 exact",
    font=fsm,
    fill=(130, 140, 155),
)
os.makedirs("_check", exist_ok=True)
img.save("_check/ease_land_curve_compare.png")
print("saved _check/ease_land_curve_compare.png")
