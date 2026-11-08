# -*- coding: utf-8 -*-
"""独立进程的屏幕竖条取样器（win-cover-3 专用）。

为什么要独立进程：动画期间主进程的 Python 主线程持续占 GIL，同进程线程取样会被
饿到 50~100ms 一次（实测），既看不清 165Hz 的保持阶梯，又会反向扰动被测动画。
独立进程 + timeBeginPeriod(1) 可稳到 1~2ms 一次。

输出：每行 {"pc": <绝对 perf_counter 秒>, "y": <窗口顶边物理像素，-1 表示整条都是背景>}

用法：
  python devtools/_c3_edge_probe.py --x 1600 --y 0 --w 2 --h 1000 --dur 40 --out _check/x.jsonl
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import time

import numpy as np

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_user32.GetDC.restype = wt.HDC
_user32.GetDC.argtypes = [wt.HWND]
_gdi32.CreateCompatibleDC.restype = wt.HDC
_gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
_gdi32.BitBlt.restype = wt.BOOL
_gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int,
                          wt.DWORD]
_gdi32.SelectObject.restype = wt.HANDLE
_gdi32.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
_user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class _BI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BIH), ("bmiColors", wt.DWORD * 3)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", type=int, required=True)
    ap.add_argument("--y", type=int, default=0)
    ap.add_argument("--w", type=int, default=2)
    ap.add_argument("--h", type=int, default=1000)
    ap.add_argument("--dur", type=float, default=40.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--delay", type=float, default=0.0)
    a = ap.parse_args()

    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:                                  # noqa: BLE001
        pass

    hs = _user32.GetDC(None)
    hm = _gdi32.CreateCompatibleDC(hs)
    bi = _BI()
    bi.bmiHeader.biSize = ctypes.sizeof(_BIH)
    bi.bmiHeader.biWidth = a.w
    bi.bmiHeader.biHeight = -a.h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bits = ctypes.c_void_p()
    _gdi32.CreateDIBSection.restype = wt.HANDLE
    _gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                        ctypes.POINTER(ctypes.c_void_p),
                                        wt.HANDLE, wt.DWORD]
    hb = _gdi32.CreateDIBSection(hs, ctypes.byref(bi), 0, ctypes.byref(bits),
                                 None, 0)
    _gdi32.SelectObject(hm, hb)
    buf = (ctypes.c_ubyte * (a.w * a.h * 4)).from_address(bits.value)
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(a.h, a.w, 4)

    if a.delay > 0:
        time.sleep(a.delay)

    out = open(a.out, "w", encoding="utf-8", buffering=1)
    t_end = time.perf_counter() + a.dur
    n = 0
    while time.perf_counter() < t_end:
        t = time.perf_counter()
        _gdi32.BitBlt(hm, 0, 0, a.w, a.h, hs, a.x, a.y, 0x00CC0020)
        r = arr[:, :, 2].astype(np.int16)
        g = arr[:, :, 1].astype(np.int16)
        b = arr[:, :, 0].astype(np.int16)
        is_bg = ((r - g) > 60) & ((b - g) > 60)
        hit = np.nonzero(~is_bg.all(axis=1))[0]
        top = int(hit[0]) if hit.size else -1
        out.write(json.dumps({"pc": round(t, 6), "y": top}) + "\n")
        n += 1
        time.sleep(0)
    out.close()
    print(f"edge samples {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
