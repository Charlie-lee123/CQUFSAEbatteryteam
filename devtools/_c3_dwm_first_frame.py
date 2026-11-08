# -*- coding: utf-8 -*-
"""覆盖层「show() → DWM 首次合成上屏」延迟实测（win-cover-3，用于判定 100ms 是否必要）。

做法：直接调用产品自己的 `_begin_window_cover()`（探针只传一张纯色图，不改源码），
show() 前后用 perf_counter 打点，然后以 ~0.2ms 周期 BitBlt 1x1 像素轮询该点，
第一次看到纯色即"上屏"。重复 N 次取分位。

跑法：.venv/Scripts/python.exe devtools/_c3_dwm_first_frame.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings, Qt            # noqa: E402
from PySide6.QtGui import QColor, QPixmap          # noqa: E402
from PySide6.QtWidgets import QApplication         # noqa: E402

_u = ctypes.WinDLL("user32", use_last_error=True)
_g = ctypes.WinDLL("gdi32", use_last_error=True)
_u.GetDC.restype = wt.HDC
_u.GetDC.argtypes = [wt.HWND]
_g.CreateCompatibleDC.restype = wt.HDC
_g.CreateCompatibleDC.argtypes = [wt.HDC]
_g.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                      ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
_g.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
_g.CreateDIBSection.restype = wt.HANDLE
_g.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class _BI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BIH), ("bmiColors", wt.DWORD * 3)]


def make_pixel_reader():
    hs = _u.GetDC(None)
    hm = _g.CreateCompatibleDC(hs)
    bi = _BI()
    bi.bmiHeader.biSize = ctypes.sizeof(_BIH)
    bi.bmiHeader.biWidth = 1
    bi.bmiHeader.biHeight = -1
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bits = ctypes.c_void_p()
    hb = _g.CreateDIBSection(hs, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
    _g.SelectObject(hm, hb)
    buf = (ctypes.c_ubyte * 4).from_address(bits.value)

    def read(x, y):
        _g.BitBlt(hm, 0, 0, 1, 1, hs, x, y, 0x00CC0020)
        return buf[2], buf[1], buf[0]      # R,G,B

    return read


def main() -> int:
    snap = {}
    st = QSettings("MimoWorks", "SerialAssistant")
    for k in st.allKeys():
        snap[k] = st.value(k)

    app = QApplication(sys.argv[:1])
    import ui_anim
    import customization
    ui_anim.set_frame_hz(int(customization.get("anim_fps")))
    from main_window import MainWindow

    win = MainWindow()
    win._port_watch.stop()
    win.show()
    app.processEvents()
    time.sleep(0.3)

    read = make_pixel_reader()
    g = win.geometry()
    dpr = win.devicePixelRatioF()
    px = int((g.x() + g.width() // 2) * dpr)
    py = int((g.y() + g.height() // 2) * dpr)

    # 先看 z 序：覆盖层是否真的在主窗之上
    demo = QPixmap(g.width(), g.height())
    demo.fill(QColor(255, 0, 255))
    demo.setDevicePixelRatio(dpr)
    lab = win._begin_window_cover(demo, g, with_bg=False, want_crossfade=False)
    app.processEvents()
    time.sleep(0.15)
    print("z序：主窗不透明时读到", read(px, py), "（若=主窗色 说明覆盖层在其下）")
    win.setWindowOpacity(0.0)
    app.processEvents()
    time.sleep(0.12)
    print("z序：主窗 opacity=0 后读到", read(px, py), "（洋红=覆盖层已合成就绪）")
    win.setWindowOpacity(1.0)
    win._finish_window_cover()
    app.processEvents()
    time.sleep(0.1)

    res = []
    colors = [(255, 0, 255), (0, 255, 0), (255, 255, 0), (0, 255, 255)]
    for i in range(24):
        c = colors[i % len(colors)]
        pix = QPixmap(g.width(), g.height())
        pix.fill(QColor(*c))
        pix.setDevicePixelRatio(dpr)
        t0 = time.perf_counter()
        lab = win._begin_window_cover(pix, g, with_bg=False, want_crossfade=False)
        if lab is None:
            print("cover 建立失败（动画被关？）")
            break
        t_show = time.perf_counter()
        seen = None
        while time.perf_counter() - t_show < 0.5:
            r, gg, b = read(px, py)
            if abs(r - c[0]) < 12 and abs(gg - c[1]) < 12 and abs(b - c[2]) < 12:
                seen = time.perf_counter()
                break
            time.sleep(0.0002)
        if seen is not None:
            res.append((seen - t_show) * 1000.0)
        elif i == 0:
            print("DEBUG label_geo=", lab.geometry(), "visible=", lab.isVisible(),
                  "px=", (px, py), "读到=", read(px, py), "期望=", c,
                  "win_geo=", win.geometry(), "dpr=", dpr)
        # 收尾
        win._finish_window_cover()
        app.processEvents()
        time.sleep(0.12)

    if res:
        res_s = sorted(res)
        print(f"n={len(res)}  首帧 p50={statistics.median(res_s):.2f}ms  "
              f"p95={res_s[int(len(res_s) * 0.95)]:.2f}ms  max={res_s[-1]:.2f}ms  "
              f"min={res_s[0]:.2f}ms")
        print("全部:", [round(x, 1) for x in res])
    win._stop_snap_worker()
    win.close()
    app.processEvents()

    q2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(q2.allKeys()) - set(snap):
        q2.remove(k)
    for k, v in snap.items():
        q2.setValue(k, v)
    q2.sync()
    print("设置已还原")
    return 0


if __name__ == "__main__":
    sys.exit(main())
