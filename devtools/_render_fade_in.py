import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PIL import Image, ImageDraw
from PIL.ImageQt import fromqimage
import splash_motion as sm
app = QGuiApplication(sys.argv)
W, H = 900, 560
def render(r, t):
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(0x0d0d0f)
    p = QPainter(img)
    r.paint(p, W, H, t)
    p.end()
    return fromqimage(img)
r = sm.MotionRenderer("dark", 2.0, W, H, sm.SPEC_PLANA)
time.sleep(1.5); r._absorb_pending()
ts = [0.30, 0.50, 0.70, 0.85, 1.00, 1.20, 1.50, 1.70]
cells = [render(r, t) for t in ts]
labels = [f"IN t={t:.2f}" for t in ts]
ncols=4; pad=6
nrows=(len(cells)+ncols-1)//ncols
mont = Image.new("RGB",(ncols*(W+pad)+pad, nrows*(H+pad)+pad),(20,20,24))
d=ImageDraw.Draw(mont)
for i,c in enumerate(cells):
    row,col=divmod(i,ncols); x=pad+col*(W+pad); y=pad+row*(H+pad)
    mont.paste(c,(x,y)); d.text((x+4,y+4),labels[i],fill=(230,230,235))
mont.save("_check/planA_fade_in.png"); print("saved", mont.size)
