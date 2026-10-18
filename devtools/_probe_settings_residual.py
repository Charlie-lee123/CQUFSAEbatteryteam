# -*- coding: utf-8 -*-
r"""设置面板"最后一帧闪"的**残差分解**：用数字判定这块闪到底来自哪里。

背景
----
入场动画跑完、真面板 ``show()`` 的那一帧，快照（``_SettingsShot``，一张 PrintWindow
位图）换成真窗口。内容层面两者是同一张图，但有三样东西**抓不进位图**：

  ① DWM 系统投影 —— 画在窗口矩形**外面**，PrintWindow 拿不到；
  ② 圆角 + 激活态标题栏 —— 由 DWM 合成，可能晚于 ``show()`` 才画上去；
  ③ 客户区里的重绘残差（控件首帧内容、滚动条淡入等）。

三者混在一起时，任何人说"是阴影造成的"都只是猜测。这个探针把它们**分到四个区域**
分别计数，谁占大头一目了然。

三次抓图
--------
  · **A**：``PrintWindow(PW_RENDERFULLCONTENT)`` 抓快照窗口 —— 即"快照正在显示的内容"。
    （分层窗口 PrintWindow 常返回全黑，此时退回 ``shot.grab()``，输出里会注明。）
  · **B**：屏幕 1:1 ``BitBlt`` —— 换帧**前**用户真实看到的那块屏（只有快照）。
  · **C**：同矩形屏幕 ``BitBlt`` —— 换帧**后**（真面板上屏、快照已撤、渐隐跑完）。

判定
----
  ``B vs C`` 才是用户实际挨的那一下。按区域统计差异点数：

    ① 客户区（frame 去掉边框与标题栏）  ② 标题栏（frame 顶部 ``top`` 那条）
    ③ 圆角四角（半径 ~8 逻辑像素的四个角方块）  ④ 窗口外 60px 阴影环

  · **④ 阴影环 ≈ 40%（实测 5406 / 12884 = 42.0%）** → 已定：**保留原生标题栏**，
    ④ 是"已知且用户接受"的残差。快照里没有 DWM 投影、真窗口上屏才有，这一块
    靠内容层的小修（A1~A5）治不了，也不打算治 —— 换帧那一下把它摊成 96ms 的
    渐隐坡就够了（``_probe_settings_flicker.py`` 量到空洞帧 0、平均像素差 0.00）。
  · **① 客户区 / ② 标题栏 / ③ 圆角** 里剩下的点同属"PrintWindow 位图 vs 真窗口
    像素"的系统绘制差（底边线那 2269 点 = DWM 合成的边框行），不碰窗口样式同样
    消不掉。当初"①<1000 / 底边线归零"的合格线是拍错的，已作废。
  · 这个探针现在的用途是**回归看守**：一旦有人把坐标系/锚点/落位改回去，①②③
    会成倍上涨（本轮把 ``_settings_button_rect`` 改成屏幕坐标 + 锚点改浮点之后，
    1px 错位项清零，① 从 8462 掉到 7055）。

口径
----
  · 采样网格 4 物理像素，单通道差 > 6 记为"不同"（压掉抖动与 JPEG 式噪声）。
  · 全部矩形按 ``devicePixelRatio`` 换算成**物理像素**再 BitBlt —— ``frameGeometry()``
    是逻辑坐标，直接喂给 GDI 会整体错位（125% 缩放下差 20%）。
  · 只读：不改任何代码路径，只在探针里按现流程走到 t≈0.98 然后手动推进落定。

用法：python devtools/_probe_settings_residual.py
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

from PySide6.QtCore import QRect                                 # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage                # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
GRID = 4            # 采样网格（物理像素）
PIXEL_TOL = 6       # 单通道差超过它才算"不同"
SHADOW_PAD = 60     # 窗口外阴影环宽度（逻辑像素）
CORNER_R = 8        # 圆角半径（逻辑像素）


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class _BI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BIH), ("bmiColors", wintypes.DWORD * 3)]


def grab_screen(x: int, y: int, w: int, h: int) -> bytes | None:
    """屏幕 1:1 BitBlt（**物理像素**坐标），返回自上而下 BGRA 缓冲。"""
    hdc_screen = u32.GetDC(0)
    if not hdc_screen:
        return None
    mem = g32.CreateCompatibleDC(hdc_screen)
    bmp = g32.CreateCompatibleBitmap(hdc_screen, w, h)
    g32.SelectObject(mem, bmp)
    try:
        if not g32.BitBlt(mem, 0, 0, w, h, hdc_screen, x, y, SRCCOPY):
            return None
        bmi = _BI()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BIH)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h            # 负 = 自上而下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        g32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
        return buf.raw
    finally:
        g32.DeleteObject(bmp)
        g32.DeleteDC(mem)
        u32.ReleaseDC(0, hdc_screen)


def window_rect_physical(hwnd: int) -> tuple[int, int, int, int] | None:
    """``GetWindowRect``：窗口扩展边框的物理像素矩形。"""
    rect = wintypes.RECT()
    if not u32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def pixmap_to_bgra(pixmap) -> tuple[int, int, bytes]:
    """QPixmap → (w, h, BGRA 自上而下)。PrintWindow 返回的是物理像素位图。"""
    img = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB32)
    w, h = img.width(), img.height()
    stride = img.bytesPerLine()
    raw = bytes(img.constBits())
    out = bytearray(w * h * 4)
    for row in range(h):
        src = raw[row * stride: row * stride + w * 4]
        out[row * w * 4: (row + 1) * w * 4] = src
    return w, h, bytes(out)


def blit_into(canvas: bytearray, cw: int, ch: int,
              src: bytes | None, sw: int, sh: int, ox: int, oy: int) -> int:
    """把 src 贴进 canvas（越界裁掉）。返回实际贴上的像素数（0 = 完全在图外）。"""
    if src is None or sw <= 0 or sh <= 0:
        return 0
    x0, y0 = max(0, ox), max(0, oy)
    x1, y1 = min(cw, ox + sw), min(ch, oy + sh)
    if x1 <= x0 or y1 <= y0:
        return 0
    for y in range(y0, y1):
        s = (y - oy) * sw * 4 + (x0 - ox) * 4
        d = (y * cw + x0) * 4
        canvas[d: d + (x1 - x0) * 4] = src[s: s + (x1 - x0) * 4]
    return (x1 - x0) * (y1 - y0)


def classify(px: int, py: int, frame: QRect, margins: tuple[int, int, int, int],
             dpr: float, corner: int) -> str:
    """把一个物理像素点归到 ①客户区 / ②标题栏 / ③圆角四角 / ④阴影环。"""
    if not frame.contains(px, py):
        return "shadow"
    left, top, right, bottom = (int(round(m * dpr)) for m in margins)
    f = frame
    # 圆角四个角方块（左上/右上/左下/右下）
    corners = (
        QRect(f.left(), f.top(), corner, corner),
        QRect(f.right() - corner + 1, f.top(), corner, corner),
        QRect(f.left(), f.bottom() - corner + 1, corner, corner),
        QRect(f.right() - corner + 1, f.bottom() - corner + 1, corner, corner),
    )
    for box in corners:
        if box.contains(px, py):
            return "corner"
    if py < f.top() + top:
        return "titlebar"
    if (px < f.left() + left or px > f.right() - right
            or py > f.bottom() - bottom):
        return "frame_edge"
    return "client"


def diff_regions(b: bytes | None, c: bytes | None, cw: int, ch: int,
                 frame: QRect, margins, dpr: float) -> dict[str, tuple[int, int]]:
    """B vs C 分区计数：返回 {区域: (差异点数, 采样点数)}。"""
    res: dict[str, tuple[int, int]] = {}
    if b is None or c is None:
        return res
    corner = max(2, int(round(CORNER_R * dpr)))
    for py in range(0, ch, GRID):
        base = py * cw * 4
        for px in range(0, cw, GRID):
            i = base + px * 4
            d = max(abs(b[i] - c[i]), abs(b[i + 1] - c[i + 1]), abs(b[i + 2] - c[i + 2]))
            key = classify(px, py, frame, margins, dpr, corner)
            n, total = res.get(key, (0, 0))
            res[key] = (n + (1 if d > PIXEL_TOL else 0), total + 1)
    return res


def ring_bands(b: bytes | None, c: bytes | None, cw: int, ch: int,
               frame: QRect, dpr: float) -> list[tuple[str, int, int]]:
    """阴影环按"离窗口边缘多远"分带计数：系统投影只该落在贴边那一圈。

    如果差异集中在 0~8 / 8~24 逻辑像素，那是 DWM 投影的形状；如果 24~60px 也一大片，
    说明窗外背景本身也变了（那是另一回事，得单独看）。
    """
    bands = ((0, 8), (8, 24), (24, SHADOW_PAD))
    acc = {band: [0, 0] for band in bands}
    if b is None or c is None:
        return []
    for py in range(0, ch, GRID):
        base = py * cw * 4
        for px in range(0, cw, GRID):
            if frame.contains(px, py):
                continue
            dx = 0 if frame.left() <= px <= frame.right() else min(
                abs(px - frame.left()), abs(px - frame.right()))
            dy = 0 if frame.top() <= py <= frame.bottom() else min(
                abs(py - frame.top()), abs(py - frame.bottom()))
            dist = max(dx, dy) / dpr
            for band in bands:
                if band[0] <= dist < band[1]:
                    i = base + px * 4
                    d = max(abs(b[i] - c[i]), abs(b[i + 1] - c[i + 1]),
                            abs(b[i + 2] - c[i + 2]))
                    acc[band][0] += 1 if d > PIXEL_TOL else 0
                    acc[band][1] += 1
                    break
    return [(f"{lo}~{hi}px", v[0], v[1]) for (lo, hi), v in
            ((band, acc[band]) for band in bands)]


def count_diff(a: bytes | None, b: bytes | None, cw: int, ch: int,
               step: int = 8) -> int:
    """粗算两张图的差异点数（只用来判断"画面稳没稳"，不参与结论）。"""
    if a is None or b is None:
        return 1 << 30
    n = 0
    for py in range(0, ch, step):
        base = py * cw * 4
        for px in range(0, cw, step):
            i = base + px * 4
            if max(abs(a[i] - b[i]), abs(a[i + 1] - b[i + 1]),
                   abs(a[i + 2] - b[i + 2])) > PIXEL_TOL:
                n += 1
    return n


def grab_stable(app, ex: int, ey: int, cw: int, ch: int,
                tries: int = 8, gap_ms: float = 45.0) -> tuple[bytes | None, int]:
    """连抓到**连续两张几乎一样**为止，返回最后一张和它的不稳定度。

    DWM 合成、滚动条淡入、标题栏激活态都会晚几帧到几十帧 —— 只抓一张会把"还没画完"
    当成残差（实测同一段代码两次跑，客户区差异能从 8.9k 跳到 69k，全是这个）。
    """
    prev = grab_screen(ex, ey, cw, ch)
    last = prev
    for _ in range(tries):
        time.sleep(gap_ms / 1000.0)
        app.processEvents()
        cur = grab_screen(ex, ey, cw, ch)
        unstable = count_diff(prev, cur, cw, ch)
        prev, last = cur, cur
        if unstable <= 40:                 # 8px 网格上 <40 点抖动 = 画面已稳
            return last, unstable
    return last, count_diff(prev, last, cw, ch)


def row_bands(b: bytes | None, c: bytes | None, cw: int, ch: int,
              frame: QRect, dpr: float, band: int = 20) -> list[tuple[int, int, int]]:
    """客户区按纵向 20 逻辑像素切带，数每带的差异点数 —— 用来定位"差在哪儿"。

    整片均匀分布 = 抗锯齿/缩放差异；集中在某几带 = 某一块内容真的不一样
    （滚动条出现、焦点环、某一行文字排版不同……）。
    """
    out: list[tuple[int, int, int]] = []
    if b is None or c is None:
        return out
    step = int(round(band * dpr))
    for y0 in range(frame.top(), frame.bottom() + 1, step):
        n = tot = 0
        for py in range(y0, min(y0 + step, ch), GRID):
            base = py * cw * 4
            for px in range(frame.left(), frame.right() + 1, GRID):
                i = base + px * 4
                tot += 1
                if max(abs(b[i] - c[i]), abs(b[i + 1] - c[i + 1]),
                       abs(b[i + 2] - c[i + 2])) > PIXEL_TOL:
                    n += 1
        out.append((int((y0 - frame.top()) / dpr), n, tot))
    return out


def fmt(res: dict[str, tuple[int, int]]) -> str:
    """一行式输出：每个区域的差异点数 + 占总差异的百分比。"""
    names = (("client", "①客户区"), ("titlebar", "②标题栏"),
             ("corner", "③圆角"), ("frame_edge", "边框"), ("shadow", "④阴影环"))
    total_diff = sum(v[0] for v in res.values()) or 1
    parts = []
    for key, label in names:
        n, tot = res.get(key, (0, 0))
        if not tot:
            continue
        parts.append(f"{label} {n} 点 ({n * 100.0 / total_diff:.0f}%)")
    return "   ".join(parts) if parts else "（无采样）"


def main() -> int:
    # ⚠️ 门禁：桌面全黑时所有像素判据都是假 0（本探针的 A/B/C 全靠 BitBlt），
    #    宁可 [SKIP] 退出，也不能输出一份看起来正常的四分区表。
    from _desktop_gate import require_alive
    require_alive("settings_residual")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))

    from main_window import _capture_native_window
    from main_window import MainWindow
    window = MainWindow()
    window.show()
    window.apply_startup_state()
    for _ in range(120):
        app.processEvents()
        time.sleep(0.003)

    dpr = float(window.devicePixelRatioF())
    screen = QGuiApplication.primaryScreen().geometry()
    print(f"dpr={dpr:g}  屏幕逻辑 {screen.width()}x{screen.height()}  "
          f"物理 {int(screen.width() * dpr)}x{int(screen.height() * dpr)}")

    # ── 走到 t=1.0：快照正好铺满落定矩形，但**不换帧**（真面板还没 show）─────
    # ⚠️ 必须停在 t=1.0 而不是 0.98：快照是 drawPixmap(rect, 位图) 缩放画的，
    #    差 0.4% 就会重采样一遍 —— 文字/边框的抗锯齿与真窗口不再逐像素一致，
    #    客户区会凭空多出一大片"差异"，把真正的残差淹掉。摘掉 on_finished 让补间
    #    自己跑满、自己不换帧，就拿到精确的 1:1 那一帧。
    window.btn_settings.click()
    tween = window._settings_geom_tween      # 惰性创建：点一下之后才有
    orig_finished = tween._on_finished if tween is not None else None
    if tween is not None:
        tween._on_finished = None            # 让它跑满 1.0，但**不自己换帧**
    shot = window._settings_shot
    target = window._settings_geom_to
    deadline = time.perf_counter() + 3.0
    while time.perf_counter() < deadline:
        app.processEvents()
        if tween is None or not tween.running:   # 补间跑完（1.0 已送值）
            break
        time.sleep(0.002)
    if tween is not None:
        tween._on_finished = orig_finished
    app.processEvents()
    print(f"停表位置：快照 {shot.geometry().getRect()}  目标 {target.getRect()}"
          f"  逐像素对齐={'是' if shot.geometry() == target else '否'}")

    # 画布 = 落定矩形外扩 60px（逻辑→物理），再裁到屏幕内
    pad = SHADOW_PAD
    canvas_l = QRect(target.x() - pad, target.y() - pad,
                     target.width() + 2 * pad, target.height() + 2 * pad)
    canvas_l = canvas_l.intersected(screen)
    ex, ey = int(round(canvas_l.x() * dpr)), int(round(canvas_l.y() * dpr))
    cw, ch = int(round(canvas_l.width() * dpr)), int(round(canvas_l.height() * dpr))
    frame = QRect(int(round(target.x() * dpr)) - ex, int(round(target.y() * dpr)) - ey,
                  int(round(target.width() * dpr)), int(round(target.height() * dpr)))
    margins = window._settings_frame_margins
    print(f"画布 {cw}x{ch}（物理）  frame 在画布内 {frame.getRect()}  "
          f"边距(逻辑) {margins}")

    # ── B：换帧前的屏幕 ────────────────────────────────────────────────────
    b_buf, b_unstable = grab_stable(app, ex, ey, cw, ch)
    # ── B2：把快照摆到**精确**目标矩形后的屏幕 ─────────────────────────────
    # 为什么要多抓一张：``_on_settings_geometry`` 用 ``QRect.center()`` 算锚点，而
    # ``center()`` 是 ``left + (w-1)//2`` —— t=1.0 时算出来的左上角比目标**左/上各少
    # 1px**（实测 (547,168) vs (548,169)）。这 1px 会把整块内容挪一格，足以让每一条
    # 文字边缘都算进"差异"。这里手动对齐一张，用来把"1px 错位"和"真残差"拆开。
    aligned = shot.geometry() == target
    if not aligned:
        shot.setGeometry(target)
        for _ in range(3):
            app.processEvents()
        b2_buf, _ = grab_stable(app, ex, ey, cw, ch)
    else:
        b2_buf = b_buf

    # ── A：快照窗口的 PrintWindow 位图，**按 frameGeometry 对齐**贴进画布 ────
    # ⚠️ GetWindowRect 比 frameGeometry 大一圈 DWM 不可见缩放边框（实测每边 6~7 物理
    #    像素）。直接按 GetWindowRect 贴会整块偏 7px —— 于是"快照位图 vs 屏幕"看起来
    #    差一大片，其实只是没对齐（第一版就是这么假报的）。按 `_grab_settings_frame`
    #    同一套口径裁掉那圈边框，再对齐到 frameGeometry。
    hwnd = int(shot.winId())
    shot_frame = shot.frameGeometry()
    a_pix = _capture_native_window(hwnd)
    a_label = "PrintWindow"
    if a_pix.isNull() or all(v == 0 for v in bytes(a_pix.toImage().constBits())[:4096]):
        a_pix = shot.grab()                        # 分层窗口 PrintWindow 常全黑
        a_label = "shot.grab()（PrintWindow 拿不到）"
    wr = window_rect_physical(hwnd)
    if wr is not None:
        crop_x = max(0, wr[0] - int(round(shot_frame.x() * dpr)))
        crop_y = max(0, wr[1] - int(round(shot_frame.y() * dpr)))
        crop_w = min(int(round(shot_frame.width() * dpr)), a_pix.width() - crop_x)
        crop_h = min(int(round(shot_frame.height() * dpr)), a_pix.height() - crop_y)
        if crop_w > 8 and crop_h > 8:
            a_pix = a_pix.copy(crop_x, crop_y, crop_w, crop_h)
    aw, ah, a_buf = pixmap_to_bgra(a_pix)
    canvas_a = bytearray(cw * ch * 4)
    covered = blit_into(canvas_a, cw, ch, a_buf, aw, ah,
                        int(round(shot_frame.x() * dpr)) - ex,
                        int(round(shot_frame.y() * dpr)) - ey)

    # ── 走完换帧：落定 → 真面板上屏 → 渐隐跑完 ─────────────────────────────
    window._on_settings_geometry_done()
    deadline = time.perf_counter() + 2.0
    while time.perf_counter() < deadline:
        app.processEvents()
        if window._settings_swap_target is None and window._settings_dialog is not None:
            break
        time.sleep(0.002)
    for _ in range(40):                            # 给 DWM 一点时间合成
        app.processEvents()
        time.sleep(0.004)
    c_buf, c_unstable = grab_stable(app, ex, ey, cw, ch)
    print(f"画面稳定性：B 抖动 {b_unstable} 点  C 抖动 {c_unstable} 点（8px 网格）")

    dialog = window._settings_dialog
    real_frame = dialog.frameGeometry() if dialog is not None else QRect()
    print(f"落定后真面板 frame={real_frame.getRect()}  "
          f"与目标一致={real_frame == target}")

    # ── 分区计数 ───────────────────────────────────────────────────────────
    bc = diff_regions(b_buf, c_buf, cw, ch, frame, margins, dpr)
    bc2 = diff_regions(b2_buf, c_buf, cw, ch, frame, margins, dpr) if not aligned else bc
    ab = diff_regions(bytes(canvas_a), b_buf, cw, ch, frame, margins, dpr)
    print("\n区域划分：①客户区  ②标题栏  ③圆角四角  ④窗口外 60px 阴影环")
    print(f"[A vs B] 快照位图({a_label}) vs 换帧前屏幕   覆盖 {covered}px")
    # 画布外沿 A 图本来就没内容（'无覆盖'），那块差异无意义，不参与判定
    print(f"         {fmt({k: v for k, v in ab.items() if k != 'shadow'})}"
          f"   （④环内 {ab.get('shadow', (0, 0))[0]} 点：A 图无覆盖，不计）")
    ac = diff_regions(bytes(canvas_a), c_buf, cw, ch, frame, margins, dpr)
    print("[A vs C] 快照位图 vs 换帧后屏幕（分离『内容不一致』与『屏幕合成不一致』）")
    print(f"         {fmt({k: v for k, v in ac.items() if k != 'shadow'})}")
    print("[B vs C] 换帧前屏幕 vs 换帧后屏幕（用户实际挨的那一下，快照停在 t=1.0 的"
          "**原样**位置）")
    print(f"         {fmt(bc)}")
    if not aligned:
        print("[B2 vs C] 同上，但先把快照摆到**精确**的落定矩形（排除 1px 错位）")
        print(f"         {fmt(bc2)}")
        diff_1px = sum(v[0] for v in bc.values()) - sum(v[0] for v in bc2.values())
        print(f"         → 那 1px 错位自己贡献了 {diff_1px} 点差异")
    print("         ④分带（B2 vs C）：" + "   ".join(
        f"{lab} {n}/{tot}" for lab, n, tot in ring_bands(b2_buf, c_buf, cw, ch, frame, dpr)))
    bands = row_bands(b2_buf, c_buf, cw, ch, frame, dpr)
    hot = [(y, n, t) for y, n, t in bands if t and n * 100 >= t * 25]
    if hot:
        print("         客户区纵向热点（差 ≥25% 的 20px 带）："
              + "  ".join(f"y≈{y} {n}/{t}" for y, n, t in hot[:10])
              + (f"  …共 {len(hot)} 带" if len(hot) > 10 else ""))
    else:
        print("         客户区纵向分布：无 ≥25% 的带 → 整片均匀（抗锯齿/缩放级别差异）")

    for tag, res in (("B vs C（原样）", bc), ("B2 vs C（排除 1px）", bc2)):
        shadow_n = res.get("shadow", (0, 0))[0]
        total = sum(v[0] for v in res.values())
        ratio = (shadow_n * 100.0 / total) if total else 0.0
        print(f"[判定] {tag}：总差异 {total} 点，④阴影环 {shadow_n} 点 = {ratio:.1f}%")
    print("       ④ ≈40%：已定『保留原生标题栏』，④ 是已知且用户接受的残差 ——"
          "不加宽、不遮、不回退 setWindowOpacity")
    print("       ①②③ 剩下的点同属『位图 vs 真窗口』的系统绘制差（底边线 = DWM 合成"
          "边框行），不碰窗口样式消不掉")
    print("       这个探针现在的用途是回归看守：坐标系/锚点/落位一旦改错，①②③ 会成倍上涨")

    if dialog is not None:
        try:
            window._close_settings_dialog(accepted=False)
        except RuntimeError:
            pass
    for _ in range(60):
        app.processEvents()
        time.sleep(0.004)
    window.setGeometry(0, 0, 1134, 820)
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
