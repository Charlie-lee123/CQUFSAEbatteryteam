import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QGuiApplication
app = QGuiApplication(sys.argv)
import splash_motion as sm

ra = sm.MotionRenderer("dark", 2.0, 1920.0, 1080.0, sm.SPEC_PLANA)
rb = sm.MotionRenderer("dark", 2.0, 1920.0, 1080.0, sm.SPEC_PLANB)
print("SPEC_PLANA is SPEC_PLANB ->", sm.SPEC_PLANA is sm.SPEC_PLANB)
print("planA.land =", sm.SPEC_PLANA.land, " planB.land =", sm.SPEC_PLANB.land)
print("planA t_total =", sm.SPEC_PLANA.t_total, " planB t_total =", sm.SPEC_PLANB.t_total)
print()
fmt = lambda t, st: (f"t={t:4.2f}  x={st[0]:.4f} y={st[1]:.4f} h={st[2]:.4f} clip={st[3]:.3f} sig={st[4]:.2f} a={st[5]:.2f}")
for t in [0.0, 0.3, 1.0, 1.7, 2.3, 2.5, 3.0, 3.5, 4.0]:
    print("planA", fmt(t, ra._state(t, 1.6)))
print()
for t in [0.0, 0.3, 1.0, 1.7, 2.3, 2.5, 3.0, 3.2, 3.5, 4.0]:
    print("planB", fmt(t, rb._state(t, 1.6)))
