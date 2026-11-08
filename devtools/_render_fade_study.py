import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QGuiApplication, QImage, QPainter
import splash_motion as sm
from PIL import Image

app = QGuiApplication(sys.argv)
W, H = 900, 560
def render(renderer, t, fade_k=0.0):
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(0x0d0d0f)
    p = QPainter(img)
    if fade_k > 0:
        renderer.set_fade(fade_k)
    else:
        renderer.set_fade(0.0)
    renderer.paint(p, W, H, t)
    p.end()
    return img

# planA renderer
r = sm.MotionRenderer("dark", 2.0, W, H, sm.SPEC_PLANA)

# Fade-IN frames
fin_ts = [0.30, 0.40, 0.50, 0.60, 0.70, 0.85, 1.00]
# Fade-OUT frames (at hold t_total=2.5, vary _fade_k)
fout_ks = [0.0, 0.25, 0.5, 0.75, 1.0]
# also a planB landed end for comparison
rb = sm.MotionRenderer("dark", 2.0, W, H, sm.SPEC_PLANB)

cells = []
labels = []
for t in fin_ts:
    cells.append(render(r, t, 0.0)); labels.append(f"IN t={t:.2f}")
for k in fout_ks:
    cells.append(render(r, 2.5, k)); labels.append(f"OUT k={k:.2f}")
# planB end (landed) + its fade
cells.append(render(rb, 3.2, 0.0)); labels.append("planB end")
cells.append(render(rb, 3.2, 0.5)); labels.append("planB OUT k=.5")

ncols = 6
cellW, cellH = W, H
pad = 6
import math
nrows = math.ceil(len(cells)/ncols)
mont = Image.new("RGB", (ncols*(cellW+pad)+pad, nrows*(cellH+pad)+pad), (20,20,24))
from PIL import ImageDraw
d = ImageDraw.Draw(mont)
for i, c in enumerate(cells):
    row, col = divmod(i, ncols)
    x = pad + col*(cellW+pad); y = pad + row*(cellH+pad)
    from PIL.ImageQt import fromqimage
    mont.paste(fromqimage(c), (x, y))
    d.text((x+4, y+4), labels[i], fill=(230,230,235))
out = "_check/planA_fade_study.png"
mont.save(out)
print("saved", out, mont.size, "cells", len(cells))
