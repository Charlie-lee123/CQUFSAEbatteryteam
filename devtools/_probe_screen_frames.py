# -*- coding: utf-8 -*-
r"""屏幕逐帧探针（2.1 后）：直接量"用户眼睛看到的那块屏"，找最后一帧的跳变。

和 `_probe_anim_frames.py` 的区别：那个量的是**控件几何**（逻辑正确性），这个量
的是**屏幕像素**（观感）。很多"最后一帧抽一下"在几何上是 2px，但屏幕上表现为
阴影/圆角/内容整体位移 —— 只有拍屏才看得见。

做法：GDI `StretchBlt` 把主窗口（或整屏）缩到 ~420px 宽，每帧和上一帧算平均
绝对差。真正顺滑的动画，这个差值随速度平滑变化；出现**孤立尖峰**（前后两帧都
很小、它突然很大）就是一次看得见的跳。

用法：python devtools\_probe_screen_frames.py
产物：_check/screen_frames.txt
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import QRect                      # noqa: E402
from PySide6.QtWidgets import QApplication                    # noqa: E402

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32

SRCCOPY = 0x00CC0020
HALFTONE = 4
DIB_RGB_COLORS = 0
BI_RGB = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class FrameGrabber:
    """把屏幕的一块区域反复抓成小图（缩放抓，开销约 1~3ms）。"""

    def __init__(self, rect: QRect, width: int = 0) -> None:
        # width=0 → **1:1 抓取**（缩放抓太慢：1600x1000 要 30ms/帧，采样率掉到
        # 20fps，落地那一帧根本抓不到）。1:1 BitBlt 约 1ms，靠"定点抽样"算差值。
        self.rect = QRect(rect)
        self.width = int(width or rect.width())
        self.height = max(1, int(rect.height() * self.width / max(1, rect.width())))
        hdc_screen = u32.GetDC(0)
        self.mem = g32.CreateCompatibleDC(hdc_screen)
        self.bmp = g32.CreateCompatibleBitmap(hdc_screen, self.width, self.height)
        g32.SelectObject(self.mem, self.bmp)
        u32.ReleaseDC(0, hdc_screen)
        self._hdc_screen = hdc_screen
        self._bmi = BITMAPINFO()
        self._bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        self._bmi.bmiHeader.biWidth = self.width
        self._bmi.bmiHeader.biHeight = -self.height
        self._bmi.bmiHeader.biPlanes = 1
        self._bmi.bmiHeader.biBitCount = 32
        self._bmi.bmiHeader.biCompression = BI_RGB
        self._buf = ctypes.create_string_buffer(self.width * self.height * 4)

    def grab(self) -> bytes | None:
        hdc = u32.GetDC(0)
        try:
            ok = g32.BitBlt(self.mem, 0, 0, self.width, self.height,
                            hdc, self.rect.x(), self.rect.y(), SRCCOPY)
        finally:
            u32.ReleaseDC(0, hdc)
        if not ok:
            return None
        g32.GetDIBits(self.mem, self.bmp, 0, self.height, self._buf,
                      ctypes.byref(self._bmi), DIB_RGB_COLORS)
        return self._buf.raw

    def close(self) -> None:
        g32.DeleteObject(self.bmp)
        g32.DeleteDC(self.mem)


def mean_abs_diff(a: bytes, b: bytes, samples: int = 3000) -> float:
    """两帧的平均绝对差（0~255）：**定点抽样**（全量比 Python 跑不动）。

    取 B 通道（每 4 字节 1 个），再按固定步长抽 ~3000 点 —— 对"整块画面在不在动"
    足够灵敏，单点噪声影响又不大。
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    n = len(a)
    stride = max(4, (n // samples) // 4 * 4)
    total = 0
    count = 0
    for i in range(0, n - 4, stride):
        total += abs(a[i] - b[i])
        count += 1
    return total / max(1, count)


def capture_run(app, grabber, trigger, duration_ms: float = 1100.0):
    """起 trigger，然后逐帧抓屏记录 (t, diff)。"""
    frames = []
    prev = grabber.grab()
    t0 = time.perf_counter()
    trigger()
    while (time.perf_counter() - t0) * 1000 < duration_ms:
        app.processEvents()
        cur = grabber.grab()
        frames.append(((time.perf_counter() - t0) * 1000, mean_abs_diff(prev, cur)))
        prev = cur
        time.sleep(0.002)
    return frames


def analyse(name: str, frames):
    out = [f"\n== {name} =="]
    if len(frames) < 5:
        return out + ["  （帧太少）"]
    diffs = [d for _t, d in frames]
    moving = [d for d in diffs if d > 0.05]
    if not moving:
        return out + ["  （画面全程没动？）"]
    moving.sort()
    med = moving[len(moving) // 2]
    # 孤立尖峰：本帧差值 > 4×中位，且前后两帧都 < 1.5×中位
    peaks = []
    for i in range(1, len(frames) - 1):
        t, d = frames[i]
        if d > max(med * 4, 0.8):
            prev_d = frames[i - 1][1]
            next_d = frames[i + 1][1]
            if prev_d < med * 1.5 and next_d < med * 1.5:
                peaks.append((t, d, prev_d, next_d))
    out.append(f"  帧数 {len(frames)}  中位差值 {med:.2f}")
    if peaks:
        for t, d, p, n in peaks[:5]:
            out.append(f"  ⚠ 孤立尖峰 t={t:.0f}ms 差值 {d:.2f}"
                       f"（前后 {p:.2f} / {n:.2f}）")
    else:
        out.append("  ✅ 没有孤立尖峰（只有平滑变化）")
    # 最后 120ms 的差值序列（"最后一帧"就看这里）
    tail = [(t, d) for t, d in frames if t > frames[-1][0] - 120]
    out.append("  最后 120ms 差值：" +
               " ".join(f"{d:.1f}" for _t, d in tail[-12:]))
    return out


def main() -> int:
    # ⚠️ 门禁：桌面全黑时抓回来全是黑帧，"逐帧变化点"会得到一片假 0。
    from _desktop_gate import require_alive
    require_alive("screen_frames")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))

    from main_window import MainWindow
    window = MainWindow()
    window.show()
    window.apply_startup_state()
    for _ in range(120):
        app.processEvents()
        time.sleep(0.003)

    geo = window.frameGeometry()
    grabber = FrameGrabber(geo)
    lines = []
    try:
        # 1) 打开设置：重点看"落地换帧"那一下
        lines += analyse("打开设置", capture_run(
            app, grabber, lambda: window.btn_settings.click(), 1000))
        for _ in range(80):
            app.processEvents()
            time.sleep(0.004)
        # 2) 关闭设置
        lines += analyse("关闭设置", capture_run(
            app, grabber, lambda: window.btn_settings.click(), 900))
        for _ in range(80):
            app.processEvents()
            time.sleep(0.004)
        # 3) 接收区最大化
        lines += analyse("接收区最大化", capture_run(
            app, grabber, lambda: window.btn_expand.click(), 1100))
        for _ in range(90):
            app.processEvents()
            time.sleep(0.004)
        # 4) 退出最大化
        lines += analyse("退出最大化", capture_run(
            app, grabber, lambda: window.btn_expand.click(), 1100))
    finally:
        grabber.close()

    text = "\n".join(lines)
    print(text)
    path = os.path.join(_ROOT, "_check", "screen_frames.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"\n→ {path}")
    window.setGeometry(0, 0, 1134, 820)
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
