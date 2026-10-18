"""桌面活性门禁 —— **任何**走屏幕 BitBlt 的探针都必须在开拍前过这一关。

为什么要有它
------------
锁屏 / 显示器休眠 / DWM 停止合成时，``GetDC(0)`` + ``BitBlt`` 抓回来的是**纯黑**。
这时候所有"逐帧变化点"的判据都会得到 0 —— 于是：
  · 「换文本没有过渡」「没有跳变」「静置 0 点」这类**否定式**判据会假 PASS；
  · 「必须有 N 帧变化」这类**肯定式**判据会假 FAIL。
2026-10-18 就同时踩了两头：桌面上午还活着（残差四分区数字有效），下午全黑，
QA 的 P2「逐帧 0 点」和 S3②「0 像素变化」都是这么来的。

判据（同 team-lead 给的）
------------------------
``max_luma > 24`` 且 ``nonzero% > 5%`` 且 ``uniq_colors > 8`` 才算"桌面活着"。
任一条不满足 → 打印 ``[SKIP] 桌面全黑`` 并 `SystemExit(2)`，**一个 PASS 都不许输出**。

用法
----
    from _desktop_gate import require_alive
    require_alive("settings_residual")     # 放在任何抓屏之前

优先用 ``_check/desktop_alive.py`` 里的 ``grab()``（team-lead 写的纯 ctypes 版）；
那个目录是临时区、可能被清掉，所以这里自带一份等价实现兜底 —— 门禁不能因为
"找不到 helper" 就静默失效。
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

# 判据门槛（和 team-lead 的口径一致）
MIN_MAX_LUMA = 24
MIN_NONZERO_PCT = 5.0
MIN_UNIQ_COLORS = 8
# 采样步长：每 7 个像素取一个（4 字节/像素），2M 像素量级下足够且够快
_SAMPLE_STEP = 4 * 7

SM_CXSCREEN, SM_CYSCREEN = 0, 1
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0


def _grab_from_check_dir(w: int, h: int) -> bytes | None:
    """优先复用 ``_check/desktop_alive.py`` 的 ``grab()``（拿不到就返回 None）。"""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    check_dir = os.path.join(here, "_check")
    if not os.path.isfile(os.path.join(check_dir, "desktop_alive.py")):
        return None
    if check_dir not in sys.path:
        sys.path.insert(0, check_dir)
    try:
        # 按需导入：``_check`` 是临时区，可能不存在
        import desktop_alive
        return desktop_alive.grab(w, h)
    except Exception:                             # noqa: BLE001  ← 兜底实现接管
        return None


def _grab_inline(w: int, h: int) -> bytes:
    """自带实现（和 ``_check/desktop_alive.grab`` 等价）。"""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    class _BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class _BI(ctypes.Structure):
        _fields_ = [("bmiHeader", _BIH), ("bmiColors", wintypes.DWORD * 3)]

    screen = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, screen, 0, 0, SRCCOPY)
    info = _BI()
    info.bmiHeader.biSize = ctypes.sizeof(_BIH)
    info.bmiHeader.biWidth = w
    info.bmiHeader.biHeight = -h                 # 负数 = 自上而下
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), DIB_RGB_COLORS)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, screen)
    return buf.raw


def screen_size() -> tuple[int, int]:
    user32 = ctypes.windll.user32
    return (int(user32.GetSystemMetrics(SM_CXSCREEN)),
            int(user32.GetSystemMetrics(SM_CYSCREEN)))


def desktop_metrics() -> tuple[int, float, int, int, int]:
    """抓一整屏并算三个判据：``(max_luma, nonzero%, uniq_colors, w, h)``。"""
    w, h = screen_size()
    if w <= 0 or h <= 0:
        return 0, 0.0, 0, max(0, w), max(0, h)
    data = _grab_from_check_dir(w, h) or _grab_inline(w, h)
    max_luma = 0
    nonzero = 0
    total = 0
    colors: set[tuple[int, int, int]] = set()
    for i in range(0, len(data) - 3, _SAMPLE_STEP):
        blue, green, red = data[i], data[i + 1], data[i + 2]
        luma = max(red, green, blue)
        max_luma = max(max_luma, luma)
        if luma > 8:
            nonzero += 1
        total += 1
        if len(colors) < 5000:
            colors.add((red, green, blue))
    pct = 100.0 * nonzero / max(1, total)
    return max_luma, pct, len(colors), w, h


def is_alive() -> bool:
    """桌面活着返回 True（三个判据全满足）。"""
    luma, pct, colors, _w, _h = desktop_metrics()
    return (luma > MIN_MAX_LUMA and pct > MIN_NONZERO_PCT
            and colors > MIN_UNIQ_COLORS)


def require_alive(tag: str) -> None:
    """开拍前的门禁。桌面全黑 → 打印 ``[SKIP]`` 并 ``SystemExit(2)``。

    ⚠️ 退出码用 **2**（不是 0）：SKIP 绝不能被汇总脚本当成"通过"。
    """
    luma, pct, colors, w, h = desktop_metrics()
    print(f"[gate] 桌面活性 {w}x{h}  max_luma={luma}  nonzero={pct:.2f}%  "
          f"uniq_colors={colors}{'+' if colors >= 5000 else ''}")
    alive = (luma > MIN_MAX_LUMA and pct > MIN_NONZERO_PCT
             and colors > MIN_UNIQ_COLORS)
    if alive:
        return
    print(f"[SKIP] 桌面全黑 —— {tag} 的像素判据不可信，本次不跑"
          f"（门槛 max_luma>{MIN_MAX_LUMA} / nonzero>{MIN_NONZERO_PCT:.0f}% / "
          f"uniq_colors>{MIN_UNIQ_COLORS}；当前 {luma} / {pct:.2f}% / {colors}）")
    raise SystemExit(2)
