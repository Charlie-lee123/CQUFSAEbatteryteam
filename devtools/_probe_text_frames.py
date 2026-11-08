"""扫描开屏帧序列，按边缘密度（文字=高频内容）找出「文字出现」的帧区间。

用法：.venv/Scripts/python.exe devtools/_probe_text_frames.py
"""
import cv2
import glob
import numpy as np
import os

DARK = os.path.join("发布", "_internal", "assets", "splash", "splash_dark_4k60_frames")
LIGHT = os.path.join("发布", "_internal", "assets", "splash", "splash_light_4k60_frames")


def scan(d: str, label: str):
    frames = sorted(glob.glob(os.path.join(d, "frame_*.jpg")))
    print(f"\n=== {label} ({len(frames)} frames) ===")
    prev = None
    for f in frames:
        img = cv2.imdecode(np.fromfile(f, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue
        small = cv2.resize(img, (480, 300))
        g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        mag = np.sqrt(gx ** 2 + gy ** 2)
        edge = float((mag > 40).mean())
        idx = int(os.path.basename(f)[6:11])
        t_ms = idx / 60.0 * 1000.0
        diff = ""
        if prev is not None:
            diff = f" d={edge - prev:+.4f}"
        prev = edge
        bar = "#" * int(edge * 200)
        print(f"  f{idx:03d} t={t_ms:6.0f}ms edge={edge:.4f}{diff} {bar}")


if __name__ == "__main__":
    if os.path.isdir(DARK):
        scan(DARK, "dark")
    if os.path.isdir(LIGHT):
        scan(LIGHT, "light")
