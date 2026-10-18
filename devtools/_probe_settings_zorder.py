r"""设置面板换帧的 **z-order 实证** + 96ms 渐隐的可视性取证。

背景
----
用户连报「打开设置界面最后一帧会闪一下」。设计意图（``_on_settings_geometry_done``
的注释）是：快照垫底 → 真面板不透明上屏 → 快照用 96ms 渐隐撤走，把"PrintWindow 位图
vs 真窗口"的残差（DWM 阴影、圆角抗锯齿、标题栏激活态）摊成一条坡。

但换帧那段里有两句话前后打架：

    :4854  self._settings_shot.raise_()      # 快照置顶
    :4861  dialog.show()
    :4862  dialog.raise_()                   # 真面板置顶 —— 把上面那句又压回去

如果 :4862 生效，真面板完全盖住快照，那条 96ms 的渐隐**全程不可见**，换帧就退化成
一帧硬切 —— 残差一次性出现，正是用户说的"闪一下"。
唯一反例：``_SettingsShot`` 是 ``Qt.WindowType.ToolTip``（``main_window.py:309``），
Qt 在 Windows 上会给它加 ``WS_EX_TOPMOST``；真带 TOPMOST 的话快照恒在最上层，
:4862 无害，闪在别处。

"是不是 TOPMOST"靠读代码猜不出来（Qt 内部行为 + DWM 合成），所以**只量不猜**，
三组证据
--------
  ① 两个 HWND 的 ``GWL_EXSTYLE``：``WS_EX_TOPMOST`` / ``WS_EX_LAYERED`` /
     ``WS_EX_TOOLWINDOW`` 三个位逐个打出来；
  ② 真面板客户区中心、以及下沿上方 20px 两处 ``WindowFromPoint`` 的归属，
     外加 ``GetWindow(..., GW_HWNDPREV)``（谁在它上面）；
  ③ 渐隐每一步的**屏幕像素**（只抓 frame 内一小块，够快才能跟得上 96ms）：
     相邻帧差 + 与终态的差，用来确认那 96ms 里屏幕上到底有没有可见变化。

判定
----
  · 最上层是 **dialog** → **A**：:4862 是元凶，渐隐全程不可见（= 一帧硬切）；
  · 最上层是 **shot**   → **B**：:4862 无害，"闪"另有来源（转查激活态/首帧时序）。

⛔ 桌面活性门禁：抓屏判据依赖像素，桌面全黑时一切差分都是假 0 —— 开头就过
   ``_desktop_gate.require_alive``，黑屏打印 ``[SKIP]`` 并 ``SystemExit(2)``，
   **一个 PASS 都不许输出**（退出码 2，绝不能被汇总脚本当成通过）。

用法：python devtools/_probe_settings_zorder.py
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

# ---------------------------------------------------------------- Win32 常量
GWL_EXSTYLE = -20
GW_HWNDPREV = 3
WS_EX_TOPMOST = 0x00000008
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
SM_CXSCREEN, SM_CYSCREEN = 0, 1

# 单像素"有差异"的门槛（每通道）：压掉抖动，比 JPEG 式噪声严
PIXEL_DIFF = 6


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BIH), ("bmiColors", wintypes.DWORD * 3)]


def _user32() -> ctypes.WinDLL:
    return ctypes.windll.user32


def exstyle_of(hwnd: int) -> int:
    """取窗口扩展样式（GWL_EXSTYLE）。"""
    fn = _user32().GetWindowLongPtrW
    fn.restype = ctypes.c_longlong
    fn.argtypes = [wintypes.HWND, ctypes.c_int]
    return int(fn(wintypes.HWND(hwnd), GWL_EXSTYLE))


def exstyle_text(style: int) -> str:
    """把三个关心的位翻译成可读文本（其余位不解释）。"""
    bits = []
    if style & WS_EX_TOPMOST:
        bits.append("TOPMOST")
    if style & WS_EX_LAYERED:
        bits.append("LAYERED")
    if style & WS_EX_TOOLWINDOW:
        bits.append("TOOLWINDOW")
    return "+".join(bits) if bits else "（三者皆无）"


def window_from_point(x: int, y: int) -> int:
    """物理屏幕坐标 (x, y) 处最上层的窗口 HWND。"""
    fn = _user32().WindowFromPoint
    fn.restype = wintypes.HWND
    fn.argtypes = [_POINT]
    return int(fn(_POINT(x, y)))


def hwnd_prev(hwnd: int) -> int:
    """z 序上**排在它上面**的那个窗口（GW_HWNDPREV）。"""
    fn = _user32().GetWindow
    fn.restype = wintypes.HWND
    fn.argtypes = [wintypes.HWND, wintypes.UINT]
    return int(fn(wintypes.HWND(hwnd), GW_HWNDPREV))


def grab_rect(x: int, y: int, w: int, h: int) -> bytes:
    """抓屏幕上一块矩形（物理坐标），返回 BGRA 字节串。

    只抓小区域是有意的：整屏 BitBlt 一次 30~40ms，96ms 的渐隐根本跟不上，
    抓回来就只剩"首/末"两帧，看不出中间态。
    """
    user32 = _user32()
    gdi32 = ctypes.windll.gdi32
    screen = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, SRCCOPY)
    info = _BI()
    info.bmiHeader.biSize = ctypes.sizeof(_BIH)
    info.bmiHeader.biWidth = w
    info.bmiHeader.biHeight = -h               # 负 = 自上而下
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), DIB_RGB_COLORS)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, screen)
    return buf.raw


def diff_stats(a: bytes, b: bytes) -> tuple[int, float]:
    """两块同尺寸 BGRA 的差异：``(超门槛点数, 平均通道差)``。"""
    n = min(len(a), len(b)) - 3
    count = 0
    total = 0
    for i in range(0, n, 4):
        d = max(abs(a[i] - b[i]), abs(a[i + 1] - b[i + 1]), abs(a[i + 2] - b[i + 2]))
        if d > PIXEL_DIFF:
            count += 1
        total += d
    samples = max(1, n // 4)
    return count, total / samples


def mean_luma(buf: bytes) -> float:
    """整块的平均亮度（0~255）—— 用来判断"渐隐"到底是**透出底下的面板**
    还是**把自己调暗成一片黑**（后者就是闪的来源）。"""
    total = 0
    n = 0
    for i in range(0, len(buf) - 3, 4):
        total += max(buf[i], buf[i + 1], buf[i + 2])
        n += 1
    return total / max(1, n)


def _dump_png(buf: bytes, w: int, h: int, tag: str) -> str | None:
    """把一块 BGRA 存成 PNG（``QA_ZORDER_DUMP=<dir>`` 时才有），供肉眼查看。"""
    out_dir = os.environ.get("QA_ZORDER_DUMP")
    if not out_dir:
        return None
    try:
        from PySide6.QtGui import QImage
        os.makedirs(out_dir, exist_ok=True)
        img = QImage(bytes(buf), w, h, QImage.Format.Format_ARGB32).copy()
        path = os.path.join(out_dir, f"{tag}.png")
        img.save(path)
        return path
    except Exception:                                  # noqa: BLE001  ← 存不了就算了
        return None


def _snapshot_qsettings() -> dict[str, object]:
    """跑之前把 QSettings 快照下来，退出时原样写回（别污染用户的配置）。"""
    from PySide6.QtCore import QSettings
    s = QSettings("MimoWorks", "SerialAssistant")
    snap = {}
    for key in s.allKeys():
        snap[str(key)] = s.value(key)
    return snap


def _restore_qsettings(snap: dict[str, object]) -> None:
    from PySide6.QtCore import QSettings
    s = QSettings("MimoWorks", "SerialAssistant")
    for key, value in snap.items():
        s.setValue(key, value)
    s.sync()


def main() -> int:
    # 门禁：像素判据在黑屏下全是假 0，宁可 SKIP 不许出假结论
    from _desktop_gate import require_alive
    require_alive("settings_zorder")
    # ⚠️ 门禁内部会 import ``_check/desktop_alive``，那一步把 ``_check`` 塞进了
    #    sys.path[0] —— 而 ``_check`` 里可能有旧的同名模块（实测有一份旧的
    #    ui_controls.py，会让 ``import main_window`` 直接 ImportError）。
    #    这里把临时目录摘掉，并把仓库根目录顶回最前面。
    sys.path[:] = [p for p in sys.path
                   if os.path.basename(os.path.normpath(p)) != "_check"]
    if _ROOT in sys.path:
        sys.path.remove(_ROOT)
    sys.path.insert(0, _ROOT)
    for mod in [m for m in list(sys.modules) if m == "ui_controls"]:
        del sys.modules[mod]

    snap = _snapshot_qsettings()
    try:
        return _run()
    finally:
        _restore_qsettings(snap)


def _run() -> int:
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

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

    dpr = float(window.devicePixelRatioF())
    screen = QGuiApplication.primaryScreen().geometry()
    print(f"dpr={dpr:g}  屏幕逻辑 {screen.width()}x{screen.height()}")

    # ── 走到 t=1.0：快照铺满落定矩形，但**不换帧**（摘掉 on_finished）────────
    window.btn_settings.click()
    tween = window._settings_geom_tween
    if tween is None:
        print("[FAIL] 点击设置按钮后没有拿到几何补间，换帧链路没起来")
        return 1
    tween._on_finished = None
    deadline = time.perf_counter() + 3.0
    while time.perf_counter() < deadline:
        app.processEvents()
        if not tween.running:
            break
        time.sleep(0.002)
    app.processEvents()

    shot = window._settings_shot
    target = window._settings_geom_to

    # 抓取区域：frame 内一块 360x240 物理像素（居中），够快也够有内容（文字 + 边框）
    rw, rh = 360, 240
    rx = int(round((target.center().x() - rw / (2 * dpr)) * dpr))
    ry = int(round((target.center().y() - rh / (2 * dpr)) * dpr))
    # ⚠️ 裁边要用**物理**屏幕尺寸（GetSystemMetrics），拿逻辑尺寸裁会把区域往左上
    #    拽一截（第一版就是这么把取样点挪偏的）。
    phys_w, phys_h = _user32().GetSystemMetrics(SM_CXSCREEN), \
        _user32().GetSystemMetrics(SM_CYSCREEN)
    rx = max(0, min(rx, phys_w - rw))
    ry = max(0, min(ry, phys_h - rh))
    print(f"抓取区域 {rw}x{rh}（物理）@({rx},{ry})   "
          f"快照 {shot.geometry().getRect()}  目标 {target.getRect()}")

    before = grab_rect(rx, ry, rw, rh)          # 换帧**前**：屏幕上只有快照

    # ── 换帧 ──────────────────────────────────────────────────────────────
    window._on_settings_geometry_done()
    dialog = window._settings_dialog
    if dialog is None:
        print("[FAIL] 换帧之后拿不到真面板")
        return 1

    shot_hwnd = int(shot.winId())
    dialog_hwnd = int(dialog.winId())
    shot_style = exstyle_of(shot_hwnd)
    dialog_style = exstyle_of(dialog_hwnd)
    active_after_show = dialog.isActiveWindow()

    print("\n[z-order 证据 ①] GWL_EXSTYLE")
    print(f"  快照  hwnd=0x{shot_hwnd:X}  exstyle=0x{shot_style:08X}  {exstyle_text(shot_style)}")
    print(f"  真面板 hwnd=0x{dialog_hwnd:X}  exstyle=0x{dialog_style:08X}  "
          f"{exstyle_text(dialog_style)}")

    # ② WindowFromPoint：客户区中心 + 下沿上方 20 逻辑像素（物理坐标）
    cx = int(round((dialog.geometry().center().x()) * dpr))
    cy = int(round((dialog.geometry().center().y()) * dpr))
    low_y = int(round((dialog.geometry().bottom() - 20) * dpr))
    p_center = window_from_point(cx, cy)
    p_low = window_from_point(cx, max(0, low_y))
    prev_of_shot = hwnd_prev(shot_hwnd)
    prev_of_dialog = hwnd_prev(dialog_hwnd)

    def _who(h: int) -> str:
        if h == shot_hwnd:
            return "快照"
        if h == dialog_hwnd:
            return "真面板"
        return f"其它(0x{h:X})"

    print("\n[z-order 证据 ②] WindowFromPoint / GW_HWNDPREV")
    print(f"  客户区中心  -> {_who(p_center)}")
    print(f"  下沿上 20px -> {_who(p_low)}")
    print(f"  快照之上的是   {_who(prev_of_shot)}")
    print(f"  真面板之上的是 {_who(prev_of_dialog)}")
    print(f"  show() 后 isActiveWindow = {active_after_show}")

    top_is_dialog = (p_center == dialog_hwnd or p_low == dialog_hwnd) \
        and p_center != shot_hwnd and p_low != shot_hwnd
    verdict = "A（快照在真面板**下面** → :4862 dialog.raise_() 是元凶，渐隐全程不可见）" \
        if top_is_dialog else \
        "B（快照在真面板**上面** → :4862 无害，闪另有来源）"
    print(f"\n  ==> 判定：{verdict}")

    # ── ③ 渐隐每一步的屏幕 ────────────────────────────────────────────────
    t0 = time.perf_counter()
    rows: list[tuple[float, float, bytes]] = []
    prev_alpha: float | None = None
    while time.perf_counter() - t0 < 2.0:
        app.processEvents()
        alpha = float(shot._alpha)
        gone = window._settings_swap_target is None
        if prev_alpha is None or abs(alpha - prev_alpha) > 1e-6 or gone:
            # ⚠️ 抓屏**之前**先把这一拍的画强制落地 + 等 DWM 合成完：否则抓到的是
            #    上一拍的画面（alpha 已经推进、屏幕还没跟上），台阶会和真实观感错位。
            #    仅探针内做，不改产品行为。
            shot.repaint()
            window._dwm_flush()
            rows.append(((time.perf_counter() - t0) * 1000.0, alpha,
                         grab_rect(rx, ry, rw, rh)))
            prev_alpha = alpha
        if gone:
            break
        time.sleep(0.0005)

    for _ in range(40):                            # 给 DWM 一点时间合成
        app.processEvents()
        time.sleep(0.004)
    final = grab_rect(rx, ry, rw, rh)

    _dump_png(before, rw, rh, "0_before_swap")
    _dump_png(final, rw, rh, "9_final")
    print("\n[渐隐取证] 每一步的屏幕（相邻帧差 / 与终态差 / 平均亮度）")
    print("   t_ms   alpha    相邻帧差(点/均差)   与终态差(点/均差)   均亮")
    print(f"  换帧前  1.000     （基准）             "
          f"{diff_stats(final, before)[0]:7d} / {diff_stats(final, before)[1]:5.2f}   "
          f"{mean_luma(before):5.1f}")
    prev_buf = before
    prev_label = "换帧前"
    for i, (t_ms, alpha, buf) in enumerate(rows):
        _dump_png(buf, rw, rh, f"{i + 1:02d}_t{int(t_ms)}_a{alpha:.3f}")
        d_prev, m_prev = diff_stats(prev_buf, buf)
        d_fin, m_fin = diff_stats(final, buf)
        step = "" if i == 0 else f"（相对{prev_label}）"
        print(f"  {t_ms:6.1f}  {alpha:5.3f}   {d_prev:7d} / {m_prev:5.2f}      "
              f"{d_fin:7d} / {m_fin:5.2f}   {mean_luma(buf):5.1f}   {step}")
        prev_buf = buf
        prev_label = f"t={t_ms:.0f}"
    print(f"  终态    ——                                      {mean_luma(final):5.1f}")
    d_all, m_all = diff_stats(before, final)
    print(f"  换帧前 vs 终态：{d_all} 点 / 均差 {m_all:.2f}")

    visible = sum(1 for i in range(1, len(rows)) if diff_stats(rows[i - 1][2], rows[i][2])[0] > 0)
    print(f"\n  渐隐期间出现可见变化的步数 = {visible} / {max(0, len(rows) - 1)}")
    print("  （0 步 ⇒ 那条 96ms 的坡一次都没上屏，换帧 = 一帧硬切）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
