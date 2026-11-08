import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PIL import Image, ImageDraw
from PIL.ImageQt import fromqimage
import splash_motion as sm

app = QGuiApplication(sys.argv)
W, H = 900, 560
def render(r, t, fade_k=0.0):
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(0x0d0d0f)
    p = QPainter(img)
    r.set_fade(fade_k)
    r.paint(p, W, H, t)
    p.end()
    return fromqimage(img)

r = sm.MotionRenderer("dark", 2.0, W, H, sm.SPEC_PLANA)
time.sleep(1.5)                      # 等异步预热完成（worker1 的 _preheat_async）
done = r._absorb_pending()
print("fade_pix ready:", r._fade_pix is not None, "pending_done:", done)

ks = [0.0, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 1.0]
cells = [render(r, 2.5, k) for k in ks]
labels = [f"OUT k={k:.2f}" for k in ks]
ncols = 4
pad = 6
nrows = (len(cells)+ncols-1)//ncols
mont = Image.new("RGB", (ncols*(W+pad)+pad, nrows*(H+pad)+pad), (20,20,24))
d = ImageDraw.Draw(mont)
for i,c in enumerate(cells):
    row,col = divmod(i,ncols)
    x = pad+col*(W+pad); y = pad+row*(H+pad)
    mont.paste(c,(x,y)); d.text((x+4,y+4),labels[i],fill=(230,230,235))
mont.save("_check/planA_fade_out_real.png")
print("saved _check/planA_fade_out_real.png", mont.size)
