"""机理隔离探针（lead 2026-10-19 定，必做① 的**机理层**证明）。

不依赖 SerialAssistant 任何代码，单独证明这条窗口合成机理：

    非分层顶层窗口上 ``painter.setOpacity(a)`` 是**把内容按 a 预乘压暗**，
    不是"透出底下的窗口" —— DWM 对不带 ``WS_EX_LAYERED`` 的窗口忽略 alpha
    通道、当不透明来合成，屏幕上看到的是 ``a×内容`` 压在**黑底**上（线性趋
    黑）。同一窗口改用 ``setWindowOpacity(a)``（Qt 会把它变成 layered），
    DWM 才按 alpha 与桌面混合 —— 均亮恒定（透出），不趋黑。

这正是设置面板渐隐旧 bug 的根因（工程师修前实测：均亮 45.1→20.8→3.0→
0.2→0.0 线性趋黑、撤快照帧 86400 点全变）。本探针把机理从应用里剥出来
单独证明：因果链更短、不碰交付码、可反复跑。工程师那组旧实现实测留作
**应用层佐证**，本探针是**机理层证明**，两层互相独立。

两组对照（同窗口、同内容、同底色，只差"alpha 由哪条路驱动"这一个变量）：
  A 组：paintEvent 里 ``painter.setOpacity(a)``，窗口**不**调
         ``setWindowOpacity``（刻意不 layering）；
  B 组：paintEvent 全亮度画，窗口调 ``setWindowOpacity(a)``（Qt 负责
         layering）。
每组 a 从 1.0 降到 0，每步抓屏量均亮 + 核 exstyle。

预期：
  A 组：均亮随 a **线性趋黑**（内容 200 → a=0 时 0），且全程非 layered；
  B 组：均亮**恒定**（DWM 按 alpha 与同亮度底色混合），a<1 时 layered。

跑法：.venv/Scripts/python.exe devtools/_qa_layer_mechanism.py
桌面必须 ALIVE（抓屏需要真屏）；BLACK 时按门禁纪律 SKIP，绝不记 PASS。
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:                     # 直接跑本文件时 devtools 上 path
    sys.path.insert(0, _HERE)

from PySide6.QtCore import Qt                # noqa: E402
from PySide6.QtGui import QColor, QPainter    # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from _desktop_gate import desktop_metrics     # noqa: E402

# ---------------------------------------------------------------- win32 抓屏

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16),
                ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter",
                                                   ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32), ("biClrImportant",
                                                 ctypes.c_uint32)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


def _mean_luma(buf: bytes) -> float:
    """整帧平均亮度（步长 16 字节，0~255）。"""
    if not buf:
        return 0.0
    total = 0
    n = 0
    for i in range(0, len(buf) - 2, 16):
        total += (buf[i + 2] * 299 + buf[i + 1] * 587 + buf[i] * 114) // 1000
        n += 1
    return total / max(1, n)


def grab_mean_luma(x: int, y: int, w: int, h: int) -> float | None:
    """抓一块物理像素区域的均亮（BitBlt + GetDIBits，纯 GDI，无 Qt 依赖）。"""
    hdc_screen = u32.GetDC(0)
    mem = g32.CreateCompatibleDC(hdc_screen)
    bmp = g32.CreateCompatibleBitmap(hdc_screen, w, h)
    g32.SelectObject(mem, bmp)
    try:
        if not g32.BitBlt(mem, 0, 0, w, h, hdc_screen, x, y, SRCCOPY):
            return None
    finally:
        u32.ReleaseDC(0, hdc_screen)
    bmi = _BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    g32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
    g32.DeleteObject(bmp)
    g32.DeleteDC(mem)
    return _mean_luma(buf.raw)


def _exstyle(hwnd: int) -> int:
    return u32.GetWindowLongW(hwnd, GWL_EXSTYLE)


def _dwm_flush() -> None:
    try:
        ctypes.windll.dwmapi.DwmFlush()
    except Exception:                           # noqa: BLE001  没 dwmapi 就跳过
        pass


# ---------------------------------------------------------------- 输出

def info(tag: str, msg: str) -> None:
    print(f"INFO  {tag:<34} [{msg}]")


def check(name: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name:<34} [{detail}]")


# ---------------------------------------------------------------- 探针窗口

BASE_GRAY = 200                    # 内容与底色同一已知亮度（预期 B 组恒定值）
PROBE_W, PROBE_H = 400, 300        # 逻辑尺寸
PROBE_X, PROBE_Y = 420, 300        # 逻辑位置（避开四角，桌面正中间偏左）


class _Backdrop(QWidget):
    """已知亮度的不透明底色（frameless + topmost，正对着探针窗口）。"""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Window
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setGeometry(PROBE_X, PROBE_Y, PROBE_W, PROBE_H)

    def paintEvent(self, event) -> None:
        if self.isVisible():
            QPainter(self).fillRect(self.rect(), _qgray(BASE_GRAY))


def _qgray(v: int) -> QColor:
    return QColor(v, v, v)


class _Probe(QWidget):
    """ToolTip + 半透明背景的顶层窗口 —— **刻意不**调 setWindowOpacity。

    A 组：paintEvent 里 ``painter.setOpacity(self._a)`` 画同一张已知亮度；
    B 组：paintEvent 全亮度画，由外层 ``setWindowOpacity(self._a)`` 驱动。
    """

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.ToolTip)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setGeometry(PROBE_X, PROBE_Y, PROBE_W, PROBE_H)
        self._a = 1.0                    # painter 侧 alpha（A 组用）
        self._use_window_opacity = False  # B 组置 True

    def paintEvent(self, event) -> None:
        if not self.isVisible():
            return
        p = QPainter(self)
        if not self._use_window_opacity:
            p.setOpacity(max(0.0, min(1.0, self._a)))
        p.fillRect(self.rect(), _qgray(BASE_GRAY))


# ---------------------------------------------------------------- 主流程

ALPHAS = (1.0, 0.8, 0.6, 0.4, 0.2, 0.0)
TOL = 12.0                               # 均亮容差（AA/合成抖动量级，判据仍一刀切）


def _run_group(app, probe: _Probe, group: str) -> list[tuple[float, float, int]]:
    """跑一组：返回 [(alpha, 均亮, exstyle)]。"""
    out: list[tuple[float, float, int]] = []
    for a in ALPHAS:
        if group == "A":
            probe._a = a
            probe._use_window_opacity = False
            probe.update()
        else:
            probe._use_window_opacity = True
            probe._a = 1.0
            probe.setWindowOpacity(max(0.0, min(1.0, a)))
            probe.update()
        app.processEvents()
        app.processEvents()              # 两拍：paint 先进表面，再等 DWM 合成
        _dwm_flush()
        dpr = probe.devicePixelRatioF()
        mean = grab_mean_luma(int(round(PROBE_X * dpr)),
                              int(round(PROBE_Y * dpr)),
                              int(round(PROBE_W * dpr)),
                              int(round(PROBE_H * dpr)))
        if mean is None:
            mean = -1.0
        out.append((a, mean, _exstyle(int(probe.winId()))))
        time.sleep(0.03)                 # 让合成稳定，避免连读同一帧
    return out


def main() -> int:
    m = desktop_metrics()
    if not (m[0] > 24 and m[1] > 5.0 and m[2] > 8):
        info("[环境] 桌面活性",
             f"BLACK（max_luma={m[0]} nonzero%={m[1]:.2f} uniq={m[2]}）"
             "—— 抓屏不可信，本探针 SKIP（不记 PASS）")
        return 2
    info("[环境] 桌面活性",
         f"ALIVE（max_luma={m[0]} nonzero%={m[1]:.2f} uniq_colors={m[2]} "
         f"{m[3]}x{m[4]}）")

    app = QApplication(sys.argv)
    backdrop = _Backdrop()
    probe = _Probe()
    backdrop.show()
    probe.show()
    probe.raise_()
    app.processEvents()
    app.processEvents()
    _dwm_flush()
    time.sleep(0.2)

    try:
        # A 组：painter.setOpacity 驱动，窗口不 layering
        a_rows = _run_group(app, probe, "A")
        probe.setWindowOpacity(1.0)       # 复位再进 B 组
        app.processEvents()
        # B 组：setWindowOpacity 驱动（Qt 负责 layering）
        b_rows = _run_group(app, probe, "B")
    finally:
        probe.close()
        backdrop.close()

    # ---- 判定
    info("机理探针/两组序列",
         "A（painter.setOpacity，非分层）："
         + "；".join(f"a={a:g}→{mn:.1f}" for a, mn, _ in a_rows)
         + " ｜ B（setWindowOpacity，layered）："
         + "；".join(f"a={a:g}→{mn:.1f}" for a, mn, _ in b_rows))

    a_layered = [bool(ex & WS_EX_LAYERED) for _a, _mn, ex in a_rows]
    b_layered = [bool(ex & WS_EX_LAYERED) for _a, _mn, ex in b_rows]
    a_ok = (all(abs(mn - BASE_GRAY * a) <= TOL for a, mn, _ in a_rows)
            and not any(a_layered))
    check("机理/A 组：painter.setOpacity 在非分层窗口上线性趋黑", a_ok,
          "预期均亮 = 200×a（a=0 → 0，全黑）且全程非 layered；实测 "
          + "；".join(f"{mn:.1f}" for _a, mn, _ in a_rows)
          + f"；容差 ±{TOL:g}；全程 layered={any(a_layered)}"
          "（预期 False）")

    b_ok = (all(abs(mn - BASE_GRAY) <= TOL for _a, mn, _ in b_rows)
            and all(b_layered[1:]))
    check("机理/B 组：setWindowOpacity 均亮恒定（DWM 按 alpha 混合）", b_ok,
          f"预期均亮恒为 {BASE_GRAY}（底色同亮度）且 a<1 时 layered；实测 "
          + "；".join(f"{mn:.1f}" for _a, mn, _ in b_rows)
          + f"；容差 ±{TOL:g}；a<1 时 layered={all(b_layered[1:])}"
          "（预期 True；a=1.0 时 Qt 摘 layered 属正常）")

    ok = a_ok and b_ok
    info("机理探针/结论",
         "「非分层顶层窗口上 painter.setOpacity 是调暗而非透出」"
         + ("**成立** —— 与设置面板渐隐旧 bug 的根因解释互证" if ok
            else "**不成立/有偏差** —— 需重新审视根因解释，报 lead+工程师"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
