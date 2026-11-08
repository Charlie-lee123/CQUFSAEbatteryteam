"""Plan B 交棒帧的「UI 逐像素真值」生成器（必须用项目 venv / PySide6）。

用户 2026-11-04：「planB 最后要收到和软件 logo 同一个位置」「要完美重合」。
此前落点靠 LAND_X/LAND_Y/LAND_W 三个手工常量 + 从 SVG 母版 LANCZOS 缩放合成，
与软件 UI 里 QSvgRenderer 画出来的 logo 存在 1px 边缘相位差：bbox 几乎重合，
但交棒淡出时两层不同像素的软边叠在一起 = 肉眼可见的"呼吸/重影"。

本脚本不再估算：直接用**真实 MainWindow 的 BrandHeader.grab()** 抓下 Qt 自己
绘制出来的 logo 物理像素，按它在屏幕上的真实物理坐标贴到纯底画布上，得到
3200x2000 的真值帧。抽帧脚本把 Plan B 的落定段替换成这张真值帧，交棒时
开屏层与主窗的 logo 就是同一份像素 —— 位置、尺寸、抗锯齿全部一致。

产物（_check/splash/masters/）：
    ui_logo_<scheme>.png     logo 裁片（物理像素，含页头背景）
    ui_truth_<scheme>.png    3200x2000 真值帧（纯底 + 上述裁片贴到真位置）
    ui_truth.json            屏幕/几何/底色真值，供渲染器与抽帧脚本读取

⛔ 与 _splash_align_probe.py 一样：QSettings 全量快照 + finally 原样还原，
   绝不污染用户配置；MainWindow 用 WA_DontShowOnScreen，不上屏、不抢焦点。

用法：
    .venv/Scripts/python.exe devtools/_splash_ui_truth.py
"""

from __future__ import annotations

import json
import os
import sys

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "_check", "splash", "masters")

from _splash_align_probe import restore, snapshot  # noqa: E402


def qimage_to_rgba(qimg: QImage):
    """QImage → numpy RGBA（Qt 是 BGRA 字节序，alpha 必须保留）。"""
    import numpy as np
    img = qimg.convertToFormat(QImage.Format.Format_ARGB32)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    raw = bytes(img.constBits())
    arr = np.ndarray((h, bpl // 4, 4), dtype=np.uint8,
                     buffer=raw)[:, :w, :]
    return arr[:, :, [2, 1, 0, 3]].copy()


def main() -> int:
    from PIL import Image
    from ui_theme import CARD_PAD, SHADOW_PAD, _PALETTES, set_scheme

    app = QApplication(sys.argv)
    shot = snapshot()
    try:
        from main_window import MainWindow

        scr = app.primaryScreen()
        sgeo = scr.geometry()
        dpr = float(scr.devicePixelRatio())
        sw, sh = round(sgeo.width() * dpr), round(sgeo.height() * dpr)
        print(f"screen logical={sgeo.width()}x{sgeo.height()}  dpr={dpr}  "
              f"physical={sw}x{sh}")

        win = MainWindow()
        win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        # ⛔ WA_DontShowOnScreen 的窗口不会真的被 WM 最大化，showMaximized() 后仍
        #    保留 QSettings 里保存的 geometry（实测 1116x164 的页头落在窗口 origin
        #    (233,60)）——那是探针伪象。真机里主窗铺满屏幕、开屏窗口也是
        #    setGeometry(screen.geometry())，两者的公共原点就是屏幕 (0,0)。
        #    这里显式设成屏幕几何，让布局宽度/边距与最大化一致。
        win.setGeometry(sgeo)
        win.showMaximized()
        for _ in range(8):
            app.processEvents()

        bh = win.brand_header
        bpos = bh.mapTo(win, bh.rect().topLeft())
        worg = QPoint(0, 0)                  # 屏幕原点，见上
        print(f"reference origin=(0,0)  brand_header pos=({bpos.x()},{bpos.y()}) "
              f"size={bh.width()}x{bh.height()}  window={win.width()}x{win.height()}")

        os.makedirs(OUT, exist_ok=True)
        result: dict = {
            "screen_physical": [sw, sh], "dpr": dpr,
            "screen_logical": [sgeo.width(), sgeo.height()],
            "window_origin_logical": [worg.x(), worg.y()],
            "brand_header_logical": [bpos.x(), bpos.y(), bh.width(), bh.height()],
            "schemes": {},
        }

        for scheme in ("light", "dark"):
            set_scheme(scheme)                 # 只改内存 token，不写 QSettings
            bh.refresh()                       # 按新主题重取 lockup 位图
            for _ in range(4):
                app.processEvents()

            lock = bh._lockup
            ldpr = float(lock.devicePixelRatio())
            lockup_w = lock.width() / ldpr
            lockup_h = lock.height() / ldpr
            left = float(CARD_PAD + SHADOW_PAD)
            top = (bh.height() - lockup_h) / 2.0     # 与 paintEvent 完全一致
            # 屏幕物理坐标（全屏开屏窗口 setGeometry(screen.geometry())）
            px = round((worg.x() + bpos.x() + left) * dpr)
            py = round((worg.y() + bpos.y() + top) * dpr)
            pw, ph = lock.width(), lock.height()     # 位图本身就是物理像素

            # ⛔ QWidget.grab()/render() 在 WA_DontShowOnScreen 的窗口上返回**全透明**
            #    （Qt 不给不可见 widget 投递绘制），实测 alpha 恒 0，不能用。
            #    改取 BrandHeader.paintEvent 真正 drawPixmap 的那张 lockup 位图：
            #    它就是 UI 显示出来的物理像素（dpr=2，绘制时 1:1 不缩放），
            #    连 QSvgRenderer 的抗锯齿边缘都完全一致。
            import numpy as np
            rgba = qimage_to_rgba(lock.toImage())
            if rgba.shape[:2] != (ph, pw):
                raise SystemExit(f"[{scheme}] lockup 位图尺寸不符: {rgba.shape[:2]} != {(ph, pw)}")
            ys, xs = np.where(rgba[..., 3] > 16)
            ink_local = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
            print(f"[{scheme}] lockup phys={pw}x{ph} logical={lockup_w:.2f}x{lockup_h:.2f}  "
                  f"box physical=({px},{py},{pw},{ph})  ink_local={ink_local}")

            bg_hex = _PALETTES[scheme]["bg"].lstrip("#")
            bg = tuple(int(bg_hex[i:i + 2], 16) for i in (0, 2, 4))
            logo = Image.fromarray(rgba, "RGBA")
            logo.save(os.path.join(OUT, f"ui_logo_{scheme}.png"))
            truth = Image.new("RGB", (sw, sh), bg)
            truth.paste(logo, (px, py), logo)
            truth.save(os.path.join(OUT, f"ui_truth_{scheme}.png"))
            # 反推 4K 视频坐标（cover: 3840x2160 → 3200x2000）
            scale = max(sw / 3840.0, sh / 2160.0)
            ox = (sw - 3840.0 * scale) / 2.0
            oy = (sh - 2160.0 * scale) / 2.0
            result["schemes"][scheme] = {
                "bg": list(bg), "logo_box_physical": [px, py, pw, ph],
                "logo_box_logical": [left, top, lockup_w, lockup_h],
                "ink_local": ink_local,
                "video_scale": scale, "video_ox": ox, "video_oy": oy,
            }
            print(f"[{scheme}] bg={bg}  truth -> ui_truth_{scheme}.png")

        with open(os.path.join(OUT, "ui_truth.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        print("==> ui_truth.json")
        del win
        for _ in range(3):
            app.processEvents()
        return 0
    finally:
        restore(shot)


if __name__ == "__main__":
    raise SystemExit(main())
