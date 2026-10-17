# -*- coding: utf-8 -*-
"""验证：能不能用 PrintWindow 把设置面板**连原生标题栏一起**抓成位图。

动画快照用 `QWidget.grab()` 只能拿到客户区，原生标题栏（29px）在动画结束的
一瞬间凭空出现 —— 那就是用户看到的闪。PrintWindow(PW_RENDERFULLCONTENT) 走 DWM
重定向，理论上连非客户区一起渲染。两种抓法都试：

  A. 带 WA_DontShowOnScreen（不上屏、不抢焦点）
  B. 真显示但挪到屏幕外 + WA_ShowWithoutActivating（不抢焦点）

判据：位图尺寸 == frameGeometry 尺寸（说明含标题栏），且左上角不是纯背景色。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings, Qt      # noqa: E402
from PySide6.QtWidgets import QApplication     # noqa: E402

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def print_window(hwnd):
    """PrintWindow 抓一张（物理像素），返回 (bytes, w, h, ok)。"""
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return b"", 0, 0, False
    hdc = user32.GetWindowDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(memdc, bmp)
    ok = user32.PrintWindow(hwnd, memdc, 2)        # PW_RENDERFULLCONTENT
    info = BITMAPINFOHEADER()
    info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.biWidth, info.biHeight = width, -height
    info.biPlanes, info.biBitCount, info.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memdc, bmp, 0, height, buf, ctypes.byref(info), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(hwnd, hdc)
    return bytes(buf.raw), width, height, bool(ok)


def report(tag, raw, w, h, ok, fg_w, fg_h):
    from PySide6.QtGui import QImage
    img = QImage(raw, w, h, QImage.Format.Format_ARGB32).copy()
    out = os.path.join(_ROOT, "_check", f"pw_{tag}.png")
    img.save(out)
    corner = [img.pixelColor(x, y).name()
              for x, y in ((4, 4), (w // 2, 4), (4, h // 2), (w // 2, h // 2))]
    uniq = len(set(corner))
    print(f"[{tag}] PrintWindow ok={ok} 位图={w}x{h}  frame={fg_w}x{fg_h} "
          f"尺寸匹配={w == fg_w and h == fg_h}")
    print(f"        左上/上中/左中/中心 采样色: {corner}  不同色数={uniq} "
          f"-> {'有内容' if uniq > 1 else '❌ 全同色（黑屏/没抓到）'}")
    print(f"        存图: {out}")
    return img


def pump(app, s):
    end = time.perf_counter() + s
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.004)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    try:
        from selftest import make_window
        from ui_theme import apply_scheme, ui_font
        app.setFont(ui_font(12.5))
        apply_scheme("dark")

        window = make_window()
        window.show()
        pump(app, 0.5)

        from main_window import BUTTON_SPECS
        from ui_settings import SettingsDialog
        dialog = SettingsDialog("dark", 0, BUTTON_SPECS, [], parent=window)
        dialog.setModal(False)
        dialog.setMinimumWidth(500)

        # ---- A. WA_DontShowOnScreen ----
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        dialog.show()
        pump(app, 0.25)
        fg = dialog.frameGeometry()
        raw, w, h, ok = print_window(int(dialog.winId()))
        report("dontshow", raw, w, h, ok, fg.width(), fg.height())
        dialog.hide()
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)

        # ---- B. 屏幕外 + 不抢焦点 ----
        dialog.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        dialog.setGeometry(-4000, -4000, dialog.size().width(), dialog.size().height())
        dialog.show()
        pump(app, 0.35)
        fg = dialog.frameGeometry()
        raw, w, h, ok = print_window(int(dialog.winId()))
        report("offscreen", raw, w, h, ok, fg.width(), fg.height())
        # 客户区尺寸对照：若位图比客户区高出一截，说明确实含标题栏
        g = dialog.geometry()
        print(f"        客户区={g.width()}x{g.height()}  含标题栏则位图高应 ≈ {g.height()}+29")
        dialog.hide()

        window.close()
        pump(app, 0.2)
        return 0
    finally:
        for k, v in snapshot.items():
            if v is None:
                settings.remove(k)
            else:
                settings.setValue(k, v)
        settings.sync()


if __name__ == "__main__":
    sys.exit(main())
