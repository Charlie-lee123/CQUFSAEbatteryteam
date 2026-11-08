"""Plan B 落点对齐探针 —— 在本机拿「软件真实 logo」的屏幕物理矩形。

不弹窗（WA_DontShowOnScreen：窗口参与布局/最大化映射但不上屏、不抢焦点），
QSettings 先全量快照、finally 原样还原（绝不污染用户配置）。

输出：把实测 logo 墨迹矩形经 cover 变换反推成开屏视频 3840x2160 坐标系里的
LAND_X / LAND_Y / LAND_W，与 _splash_render.py 里的常量直接可比。

用法（必须项目 venv，需 PySide6）：
    .venv/Scripts/python.exe devtools/_splash_align_probe.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import winreg

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

REG_PATH = r"Software\MimoWorks\SerialAssistant"
_NZ = re.compile(rb"[\x11-\xff]")


def snapshot() -> dict:
    """winreg 原生快照：{子键路径: [(值名, 值, 类型), ...]}。"""
    acc: dict = {}

    def walk(hkey, prefix):
        vals, i = [], 0
        while True:
            try:
                vals.append(winreg.EnumValue(hkey, i))
            except OSError:
                break
            i += 1
        if vals:
            acc[prefix] = vals
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(hkey, i)
            except OSError:
                break
            with winreg.OpenKey(hkey, sub, 0, winreg.KEY_READ) as sk:
                walk(sk, prefix + sub + "\\")
            i += 1

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0,
                            winreg.KEY_READ) as k:
            walk(k, "")
    except FileNotFoundError:
        pass
    return acc


def restore(acc: dict) -> None:
    """清空应用键树后按快照原样写回（含原始注册表类型）。"""
    def clear(hkey):
        while True:
            try:
                sub = winreg.EnumKey(hkey, 0)
            except OSError:
                break
            with winreg.OpenKey(hkey, sub, 0, winreg.KEY_READ | winreg.KEY_WRITE) as sk:
                clear(sk)
            winreg.DeleteKey(hkey, sub)
        while True:
            try:
                name, _, _ = winreg.EnumValue(hkey, 0)
            except OSError:
                break
            winreg.DeleteValue(hkey, name)

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_PATH, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as root:
        clear(root)
        for path, vals in acc.items():
            key = root
            if path:
                for part in path.rstrip("\\").split("\\"):
                    key = winreg.CreateKey(key, part)
            for name, value, vtype in vals:
                winreg.SetValueEx(key, name, 0, vtype, value)
            if path:
                key.Close()


def svg_geometry(path: str) -> tuple[float, float, float, float, float, float]:
    """渲染 SVG 并量墨迹 bbox。返回 (viewBox w, h, 墨迹 x0, y0, x1, y1)。"""
    r = QSvgRenderer(path)
    if not r.isValid():
        raise SystemExit(f"SVG 无效: {path}")
    ds = r.defaultSize()
    ss = 2
    img = QImage(ds.width() * ss, ds.height() * ss,
                 QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    r.render(p)
    p.end()
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    raw = bytes(img.constBits())
    x0, y0, x1, y1 = w, -1, -1, -1
    for y in range(h):
        row = raw[y * bpl + 3: y * bpl + w * 4: 4]      # 每像素 ARGB 的 alpha 字节
        if max(row) <= 16:
            continue
        y1 = y
        if y0 < 0:
            y0 = y
        m = _NZ.search(row)
        fx = m.start()
        m2 = _NZ.search(row[::-1])
        lx = len(row) - 1 - m2.start()
        x0 = min(x0, fx)
        x1 = max(x1, lx)
    if x1 < 0:
        raise SystemExit("SVG 渲染为空")
    return (float(ds.width()), float(ds.height()),
            x0 / ss, y0 / ss, (x1 + 1) / ss, (y1 + 1) / ss)


def main() -> int:
    app = QApplication(sys.argv)
    shot = snapshot()
    try:
        # ---- 屏幕真值
        scr = app.primaryScreen()
        sgeo = scr.geometry()                    # 逻辑坐标
        dpr = scr.devicePixelRatio()
        sw, sh = round(sgeo.width() * dpr), round(sgeo.height() * dpr)
        print(f"screen logical={sgeo.width()}x{sgeo.height()}  dpr={dpr}"
              f"  physical={sw}x{sh}")

        # ---- UI lockup 素材几何（浅色版，与 BrandHeader 同源）
        svg_path = os.path.join(ROOT, "assets", "lockup_on_light.svg")
        vbw, vbh, ix0, iy0, ix1, iy1 = svg_geometry(svg_path)
        ink_w_vb, ink_h_vb = ix1 - ix0, iy1 - iy0
        print(f"lockup svg viewBox={vbw:.0f}x{vbh:.0f}  ink bbox=({ix0:.1f},{iy0:.1f})"
              f"-({ix1:.1f},{iy1:.1f})  ink={ink_w_vb:.1f}x{ink_h_vb:.1f}"
              f"  ink_aspect={ink_w_vb/ink_h_vb:.4f}  vb_aspect={vbw/vbh:.4f}")

        # ---- 真实 MainWindow（不上屏）量 BrandHeader 实际位置
        from main_window import MainWindow
        from ui_theme import CARD_PAD, SHADOW_PAD, HEADER_PAD
        win = MainWindow()
        win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        win.showMaximized()
        for _ in range(8):
            app.processEvents()

        bh = win.brand_header
        bpos = bh.mapTo(win, bh.rect().topLeft())
        inner_m = win._inner_lay.contentsMargins()
        print(f"window logical={win.width()}x{win.height()}  "
              f"isMaximized={win.isMaximized()}")
        print(f"brand_header pos=({bpos.x()},{bpos.y()}) size={bh.width()}x{bh.height()}")
        print(f"_inner_lay margins=L{inner_m.left()} T{inner_m.top()} "
              f"R{inner_m.right()} B{inner_m.bottom()}")

        lock = bh._lockup
        lw = lock.width() / lock.devicePixelRatio()
        lh = lock.height() / lock.devicePixelRatio()
        print(f"lockup pixmap phys={lock.width()}x{lock.height()} "
              f"dpr={lock.devicePixelRatio()}  logical={lw:.2f}x{lh:.2f}")

        # BrandHeader.paintEvent：left=CARD_PAD+SHADOW_PAD，竖直居中（即 HEADER_PAD 顶距）
        logo_left = bpos.x() + CARD_PAD + SHADOW_PAD
        logo_top = bpos.y() + HEADER_PAD
        print(f"logo BOX logical: left={logo_left} top={logo_top} w={lw:.2f} h={lh:.2f}")

        # 墨迹 = 盒内按 viewBox 内衬缩放
        s_ink = lw / vbw
        ink_left = logo_left + ix0 * s_ink
        ink_top = logo_top + iy0 * s_ink
        ink_w = ink_w_vb * s_ink
        ink_h = ink_h_vb * s_ink
        print(f"logo INK logical: left={ink_left:.2f} top={ink_top:.2f} "
              f"w={ink_w:.2f} h={ink_h:.2f}")

        # ---- 物理 → splash 4K cover 反推
        scale = max(sw / 3840.0, sh / 2160.0)
        ox = (sw - 3840.0 * scale) / 2.0
        oy = (sh - 2160.0 * scale) / 2.0
        land_x = ((ink_left * dpr) - ox) / scale
        land_y = ((ink_top * dpr) - oy) / scale
        land_w = (ink_w * dpr) / scale
        print(f"cover: scale={scale:.5f} ox={ox:.2f} oy={oy:.2f}")
        print(f"==> LAND_X={land_x:.1f} LAND_Y={land_y:.1f} LAND_W={land_w:.1f}"
              f"  (当前 _splash_render.py: 304.0/171.0/1144.0)")

        # 母版墨迹宽高比对照（layout.json full_vb 扣掉 4 单位 PAD）
        lay_p = os.path.join(ROOT, "_check", "splash", "masters", "layout.json")
        with open(lay_p, encoding="utf-8") as fh:
            lay = json.load(fh)
        fvb = lay["full_vb"]
        m_ink_w, m_ink_h = fvb[2] - 8.0, fvb[3] - 8.0
        print(f"masters full ink={m_ink_w:.1f}x{m_ink_h:.1f} "
              f"aspect={m_ink_w/m_ink_h:.4f}  (与 UI svg ink aspect 差 "
              f"{(m_ink_w/m_ink_h)/(ink_w_vb/ink_h_vb)-1:+.3%})")
        del win
        for _ in range(3):
            app.processEvents()
        return 0
    finally:
        restore(shot)


if __name__ == "__main__":
    raise SystemExit(main())
