"""落点段等时间采样：帧间距=速率，直观验证「先慢→后快→再慢」。"""

import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PIL import Image, ImageDraw, ImageFont
from PIL.ImageQt import fromqimage
import splash_motion as sm

app = QGuiApplication(sys.argv)
W, H, DPR = 1000, 620, 1.0
r = sm.MotionRenderer("dark", DPR, W, H, sm.SPEC_PLANB)
time.sleep(1.5)
r._absorb_pending()
s = r._spec
try:
    fb = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 22)
    fs = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 15)
except Exception:
    fb = fs = ImageFont.load_default()


def state(t):
    return r._state(t, W / H)[0:3]


ts = [s.t_hold_end + (s.t_land_end - s.t_hold_end) * i / 10 for i in range(11)]
# ---- A) 真实渲染等时间采样 montage ----
cells = []
for t in ts:
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(0x0D0D0F)
    p = QPainter(img)
    r.paint(p, W, H, t)
    p.end()
    cells.append(fromqimage(img))
ncols = 4
pad = 8
cw, ch = W // 2, H // 2
nrows = (len(cells) + ncols - 1) // ncols
mont = Image.new(
    "RGB", (ncols * (cw + pad) + pad, nrows * (ch + pad) + pad), (18, 18, 22)
)
d = ImageDraw.Draw(mont)
for i, (c, t) in enumerate(zip(cells, ts)):
    row, col = divmod(i, ncols)
    x = pad + col * (cw + pad)
    y = pad + row * (ch + pad)
    mont.paste(c.resize((cw, ch)), (x, y))
    prog = sm._ease_land((t - s.t_hold_end) / (s.t_land_end - s.t_hold_end))
    d.rectangle((x, y, x + cw, y + 26), fill=(0, 0, 0))
    d.text(
        (x + 6, y + 4),
        f"t={t:.2f}s   progress {prog * 100:5.1f}%",
        font=fs,
        fill=(120, 200, 255),
    )
mont.save("_check/planB_landing_glide.png")
print("saved _check/planB_landing_glide.png", mont.size)

# ---- B) 轨迹点密度图：41 个等时间采样点，疏=慢 密=快 ----
PW, PH = 1000, 620
pace = Image.new("RGB", (PW, PH), (16, 16, 19))
pd = ImageDraw.Draw(pace)
x0, y0, x1, y1 = 60, 70, PW - 60, PH - 70
pd.rectangle((x0, y0, x1, y1), outline=(55, 55, 62))
pd.text(
    (x0, y0 - 40),
    "Landing path — 41 equal-time samples (dense = slow, sparse = fast)",
    font=fb,
    fill=(235, 235, 240),
)
N = 40
pts = []
for i in range(N + 1):
    t = s.t_hold_end + (s.t_land_end - s.t_hold_end) * i / N
    xr, yr, hr = state(t)
    # 墨迹中心（相对屏宽/高）→ 画布坐标；y 反向让左上角在左上
    px = x0 + (x1 - x0) * min(1, max(0, xr / 0.16))
    py = y0 + (y1 - y0) * min(1, max(0, yr / 0.45))
    pts.append((px, py))


def vel(x):  # 解析导数 f'(x)=12x(1-x)^2，峰值 16/9 @ x=1/3
    return 12.0 * x * (1.0 - x) ** 2 / (16.0 / 9.0)


for i, (px, py) in enumerate(pts):
    v = vel(i / N)  # 0=静止 → 1=最快：慢=青蓝，快=橙
    col = (int(90 + 165 * v), int(190 - 50 * v), int(255 - 190 * v))
    pd.ellipse((px - 5, py - 5, px + 5, py + 5), fill=col)
pd.line(pts, fill=(45, 80, 110), width=2)
# 色标
pd.text(
    (x1 - 250, y1 - 34),
    "color: blue = slow   orange = fast",
    font=fs,
    fill=(170, 170, 185),
)
# 起点/终点标注
pd.text((pts[0][0] + 12, pts[0][1] + 6), "START (slow)", font=fs, fill=(120, 180, 230))
pd.text(
    (pts[-1][0] + 12, pts[-1][1] - 24),
    "LAND (slow settle)",
    font=fs,
    fill=(150, 200, 170),
)
pd.text(
    (x0, PH - 52),
    "OLD: symmetric peak@0.50, f''(1)=-6    |    NEW: peak@0.33, f''(1)=0 (C2), long decel tail",
    font=fs,
    fill=(130, 140, 155),
)
pace.save("_check/landing_pace.png")
print("saved _check/landing_pace.png")
