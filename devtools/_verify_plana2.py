import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QGuiApplication, QImage, QPainter
app = QGuiApplication(sys.argv)
import splash_motion as sm

# 1) state divergence re-check (after merge)
ra = sm.MotionRenderer("dark", 2.0, 1920.0, 1080.0, sm.SPEC_PLANA)
rb = sm.MotionRenderer("dark", 2.0, 1920.0, 1080.0, sm.SPEC_PLANB)
print("planA independent:", sm.SPEC_PLANA is not sm.SPEC_PLANB)
for t in [2.5, 3.0, 4.0]:
    a = ra._state(t, 1.6); b = rb._state(t, 1.6)
    print(f"  t={t}: planA pos=({a[0]:.3f},{a[1]:.3f},{a[2]:.3f})  planB pos=({b[0]:.3f},{b[1]:.3f},{b[2]:.3f})")

# 2) end-to-end paint smoke (clear-pixmap timestamps -> no async-preheat dependency)
W, H = 1280, 800
for t in [1.0, 1.7, 2.3, 2.5, 3.0]:
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(0x0d0d0f)
    p = QPainter(img)
    try:
        ra.paint(p, W, H, t)
        p.end()
    except Exception as e:
        print(f"  PAINT EXCEPTION at t={t}: {e!r}"); continue
    # count non-background pixels and their bbox
    bits = img.constBits().asarray(W*H*4) if hasattr(img.constBits(),'asarray') else None
    import struct
    # simple scan via pixelColor
    xs=[]; ys=[]
    step=4
    for y in range(0,H,step):
        for x in range(0,W,step):
            c = img.pixelColor(x,y)
            if abs(c.red()-13)+abs(c.green()-13)+abs(c.blue()-15) > 40:
                xs.append(x); ys.append(y)
    if xs:
        print(f"  t={t}: logo pixels={len(xs)} bbox x[{min(xs)}..{max(xs)}] y[{min(ys)}..{max(ys)}]  (W={W},H={H})")
    else:
        print(f"  t={t}: NO logo pixels drawn (!)")
print("SMOKE OK")
