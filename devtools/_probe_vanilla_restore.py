# -*- coding: utf-8 -*-
"""对照实验：原生 Qt 普通窗口的还原动画首帧是否也空白（2026-11-04）。"""
import os
import subprocess
import sys
import time

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "_check", "restore_frames_v2")
os.makedirs(OUT, exist_ok=True)


class Pattern(QWidget):
    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(245, 245, 245))
        p.setPen(QColor(180, 30, 30))
        f = p.font(); f.setPixelSize(60); p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, "VANILLA CONTENT")
        p.end()


def main() -> int:
    app = QApplication(sys.argv)
    scr = app.primaryScreen()
    sg = scr.geometry()
    w = Pattern()
    w.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    w.setGeometry(sg.x() + 200, sg.y() + 150, 1134, 820)
    w.show()
    QTimer.singleShot(600, w.showFullScreen)
    QTimer.singleShot(1800, w.showNormal)
    QTimer.singleShot(3000, app.quit)
    app.exec()
    return 0


if __name__ == "__main__":
    rec = os.path.join(OUT, "vanilla.mp4")
    if os.path.exists(rec):
        os.remove(rec)
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "gdigrab", "-framerate", "30",
         "-offset_x", "0", "-offset_y", "0",
         "-video_size", "3200x2000", "-i", "desktop", "-t", "8",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", rec],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    main()
    time.sleep(1.0)
    try:
        ff.stdin.write(b"q"); ff.stdin.close()
    except Exception:
        pass
    try:
        ff.wait(timeout=10)
    except Exception:
        ff.terminate()
    cap = cv2.VideoCapture(rec)
    rects = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        ys, xs = np.where(g > 40)
        rects.append((int(xs.min()), int(ys.min()), int(xs.max()-xs.min()+1),
                      int(ys.max()-ys.min()+1)) if len(xs) else (-1,-1,0,0))
    cap.release()
    n = len(rects)
    drop = None
    for i in range(1, n):
        if rects[i][2] > 0 and rects[i-1][2] > 3000 and rects[i][2] < rects[i-1][2] - 300:
            drop = i; break
    print(f"frames={n} drop={drop}")
    lo, hi = max(0,(drop or 0)-3), min(n,(drop or n)+14)
    cap = cv2.VideoCapture(rec); idx = 0
    for i in range(lo, hi):
        print(f"f{i:04d} {rects[i]}")
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if lo <= idx < hi:
            cv2.imwrite(os.path.join(OUT, f"v{idx:04d}.png"), fr)
        idx += 1
    cap.release()
    print("saved")
