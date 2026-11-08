"""GDI 抓帧吞吐基准 + 当前桌面取样（只读，不碰产品源码）。

用法：.venv\\Scripts\\python.exe devtools/_winfx_grab_bench.py
输出：_check/_winfx_desktop.png（当前屏幕 1600x1000 下采样）+ 每帧耗时统计。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

import cv2
import numpy as np

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB = 0

user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateDIBSection.restype = wt.HANDLE
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE,
                                   wt.DWORD]
gdi32.SelectObject.restype = wt.HANDLE
gdi32.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
gdi32.BitBlt.restype = wt.BOOL
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int,
                         wt.DWORD]
gdi32.DeleteObject.argtypes = [wt.HANDLE]
gdi32.DeleteDC.argtypes = [wt.HDC]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wt.DWORD * 3)]


class Grabber:
    def __init__(self, w: int, h: int) -> None:
        self.w, self.h = w, h
        self.hdc_screen = user32.GetDC(None)
        self.hdc_mem = gdi32.CreateCompatibleDC(self.hdc_screen)
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h          # top-down
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = BI_RGB
        self.bits = ctypes.c_void_p()
        self.hbmp = gdi32.CreateDIBSection(
            self.hdc_screen, ctypes.byref(bi), DIB_RGB_COLORS,
            ctypes.byref(self.bits), None, 0)
        gdi32.SelectObject(self.hdc_mem, self.hbmp)
        self.buf = (ctypes.c_ubyte * (w * h * 4)).from_address(self.bits.value)

    def grab(self) -> np.ndarray:
        gdi32.BitBlt(self.hdc_mem, 0, 0, self.w, self.h, self.hdc_screen,
                     0, 0, SRCCOPY | CAPTUREBLT)
        return np.frombuffer(self.buf, dtype=np.uint8).reshape(
            self.h, self.w, 4)

    def close(self) -> None:
        gdi32.DeleteObject(self.hbmp)
        gdi32.DeleteDC(self.hdc_mem)
        user32.ReleaseDC(None, self.hdc_screen)


def main() -> None:
    pw, ph = 3200, 2000             # 物理像素（dpr=2）
    g = Grabber(pw, ph)
    ts: list[float] = []
    small = None
    for i in range(60):
        t0 = time.perf_counter()
        arr = g.grab()
        t1 = time.perf_counter()
        sm = cv2.resize(arr, (1600, 1000), interpolation=cv2.INTER_AREA)
        t2 = time.perf_counter()
        gray = cv2.cvtColor(sm[:, :, :3], cv2.COLOR_BGR2GRAY)
        t3 = time.perf_counter()
        ok, enc = cv2.imencode(".jpg", sm[:, :, :3],
                               [cv2.IMWRITE_JPEG_QUALITY, 85])
        t4 = time.perf_counter()
        ts.append(t1 - t0)
        if i == 59:
            small = sm
            print(f"bitblt {1e3*(t1-t0):.2f}ms resize {1e3*(t2-t1):.2f}ms "
                  f"gray {1e3*(t3-t2):.2f}ms jpg {1e3*(t4-t3):.2f}ms "
                  f"jpg_bytes {len(enc)}")
    g.close()
    a = np.array(ts) * 1000
    print(f"bitblt p50={np.percentile(a,50):.2f} p95={np.percentile(a,95):.2f} "
          f"max={a.max():.2f} ms  → 理论 {1e3/np.percentile(a,50):.0f}fps")
    cv2.imwrite("_check/_winfx_desktop.png", small[:, :, :3])
    print("saved _check/_winfx_desktop.png", small.shape)


if __name__ == "__main__":
    main()
