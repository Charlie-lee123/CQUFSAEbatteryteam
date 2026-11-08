"""DWM 首帧合成延迟实测（进程内、小区域 GDI 轮询，~0.3ms 精度）。

为什么不用录屏：录屏（ddagrab/GDI 全屏）与业务进程时钟要跨进程对齐，误差 ~1 帧。
这里在**同一进程**用 perf_counter 记录 show() 时刻，另一个线程对目标区域
（40x40，BitBlt ~0.3ms）高频轮询，第一次看到"新窗口的像素"就是 DWM 把它合成
上屏的时刻。缺点：轮询会占 GIL，但 Qt 的 show/paint 在 C++ 侧，影响可忽略。

测四组：
  A 全新顶层 QLabel（ToolTip|Frameless|TopMost，layered=false）1134x820@dpr2
  B 同 A 但 setWindowOpacity(0.99)（layered，现状覆盖层就是这种）
  C 满屏 translucent 舞台窗（1706x1066 逻辑，paintEvent 填纯色矩形）
  D 已存在、先 show 再 hide 的复用车（第二次 show 的首帧延迟）

用法：.venv\\Scripts\\python.exe devtools/_winfx_dwm_lat.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

import numpy as np                                           # noqa: E402
from PySide6.QtCore import QRect, Qt, QTimer                 # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap          # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

CHECK = ROOT / "_check"
CHECK.mkdir(exist_ok=True)

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.BitBlt.restype = wt.BOOL
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int,
                         wt.DWORD]
gdi32.SelectObject.restype = wt.HANDLE
gdi32.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
gdi32.DeleteObject.argtypes = [wt.HANDLE]
gdi32.DeleteDC.argtypes = [wt.HDC]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BI(ctypes.Structure):
    _fields_ = [("bmiHeader", BIH), ("bmiColors", wt.DWORD * 3)]


class TinyGrab:
    """抓屏幕上一个小矩形（物理像素），返回 (h,w,3) uint8。"""

    def __init__(self, x: int, y: int, w: int, h: int) -> None:
        self.x, self.y, self.w, self.h = x, y, w, h
        self.hs = user32.GetDC(None)
        self.hm = gdi32.CreateCompatibleDC(self.hs)
        bi = BI()
        bi.bmiHeader.biSize = ctypes.sizeof(BIH)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        gdi32.CreateDIBSection.restype = wt.HANDLE
        gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                           ctypes.POINTER(ctypes.c_void_p),
                                           wt.HANDLE, wt.DWORD]
        self.hb = gdi32.CreateDIBSection(self.hs, ctypes.byref(bi), 0,
                                         ctypes.byref(bits), None, 0)
        gdi32.SelectObject(self.hm, self.hb)
        self.buf = (ctypes.c_ubyte * (w * h * 4)).from_address(bits.value)

    def grab(self) -> np.ndarray:
        gdi32.BitBlt(self.hm, 0, 0, self.w, self.h, self.hs,
                     self.x, self.y, 0x00CC0020)
        return np.frombuffer(self.buf, dtype=np.uint8).reshape(
            self.h, self.w, 4)[:, :, :3]

    def close(self) -> None:
        gdi32.DeleteObject(self.hb)
        gdi32.DeleteDC(self.hm)
        user32.ReleaseDC(None, self.hs)


def poll_until(grab: TinyGrab, cond, timeout: float) -> float | None:
    """轮询到 cond(frame) 为真，返回 perf_counter 时刻。"""
    t_end = time.perf_counter() + timeout
    while time.perf_counter() < t_end:
        if cond(grab.grab()):
            return time.perf_counter()
        time.sleep(0.0002)
    return None
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.rects: list[QRect] = []

    def paintEvent(self, ev) -> None:            # noqa: N802
        p = QPainter(self)
        for r, c in self.rects:
            p.fillRect(r, c)
        p.end()


def measure(kind: str, n: int, app: QApplication) -> dict:
    scr = app.primaryScreen()
    geo = scr.geometry()
    dpr = scr.devicePixelRatio()
    # 目标窗口：1134x820 @ (233,60)（= 现状普通窗口矩形）
    rect = QRect(233, 60, 1134, 762)
    # 探测点：窗口中心 40x40（物理像素）
    pcx = int((rect.center().x()) * dpr)
    pcy = int((rect.center().y()) * dpr)
    grab = TinyGrab(pcx - 20, pcy - 20, 40, 40)

    # 用"品红"这个绝对不会出现在界面里的颜色做标记
    target = np.array([255, 0, 255], dtype=np.uint8)

    def is_magenta(fr: np.ndarray) -> bool:
        d = np.abs(fr.astype(np.int16) - target.astype(np.int16)).sum(axis=2)
        return bool((d < 40).mean() > 0.5)

    def is_not(fr: np.ndarray) -> bool:
        return not is_magenta(fr)

    out: dict[str, list[float]] = {"show_ms": []}
    widget = None
    for i in range(n):
        if kind == "stage":
            if widget is None:
                widget = Stage()
                widget.setGeometry(geo)
                widget.rects = [(rect, QColor(255, 0, 255))]
            w = widget
        else:
            w = QLabel(None)
            w.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint
                             | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
            w.setAttribute(Qt.WA_ShowWithoutActivating, True)
            w.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            if kind == "reuse" and widget is not None:
                w = widget
            pm = QPixmap(rect.size())
            pm.fill(QColor(255, 0, 255))
            w.setPixmap(pm)
            w.setGeometry(rect)
            if kind == "layered":
                w.setWindowOpacity(0.99)
            if kind == "reuse" and widget is None:
                widget = w
        app.processEvents()
        # 先确保画面上没有品红
        poll_until(grab, is_not, 1.0)
        t0 = time.perf_counter()
        w.show()
        w.raise_()
        t_seen = poll_until(grab, is_magenta, 1.5)
        app.processEvents()
        if t_seen is not None:
            out["show_ms"].append((t_seen - t0) * 1e3)
        # 关闭/隐藏
        t1 = time.perf_counter()
        w.hide()
        app.processEvents()
        t_gone = poll_until(grab, is_not, 1.5)
        if t_gone is not None:
            out.setdefault("hide_ms", []).append((t_gone - t1) * 1e3)
        if kind != "reuse":
            w.deleteLater()
            app.processEvents()
        time.sleep(0.25)
    grab.close()
    res = {"kind": kind, "n": len(out["show_ms"])}
    for k, v in out.items():
        if k == "n" or not v:
            continue
        v = sorted(v)
        res[k] = {"p50": round(statistics.median(v), 2),
                  "min": round(v[0], 2),
                  "p95": round(v[int(len(v) * 0.95) - 1], 2),
                  "max": round(v[-1], 2),
                  "all": [round(x, 1) for x in v]}
    print(json.dumps(res, ensure_ascii=False))
    return res


def dwm_flush_interval(n: int = 60) -> dict:
    import ctypes as C
    dwm = C.WinDLL("dwmapi")
    dwm.DwmFlush.restype = C.c_long
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        dwm.DwmFlush()
        ts.append((time.perf_counter() - t) * 1e3)
    ts_sorted = sorted(ts)
    return {"DwmFlush_p50": round(statistics.median(ts), 2),
            "min": round(ts_sorted[0], 2),
            "max": round(ts_sorted[-1], 2)}


def main() -> None:
    app = QApplication(sys.argv[:1])
    out = {"dwm_flush": dwm_flush_interval()}
    print("dwm_flush", json.dumps(out["dwm_flush"]))
    for kind in ("opaque", "layered", "stage", "reuse"):
        out[kind] = measure(kind, 12, app)
    (CHECK / "_winfx_dwm_lat.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
