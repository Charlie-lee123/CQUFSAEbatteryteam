# -*- coding: utf-8 -*-
r"""页头 / 标题栏**透明化**验证：页头区域像素必须 == 窗口背景色。

背景：用户对着页头截图说"这里背景为什么要改，给我改回去，透明的"——
上一轮给 ``ui_brand.CaptionBar`` / ``ui_brand.BrandHeader`` 铺的 chrome 材质底
（``ui_surface.paint_chrome_material``，已随本次改动删除）全部回退，页头直接坐在
页面背景上。页面背景是**纯色**（``ui_backdrop``：两个主题都只填 ``bg`` 一个
token），所以"透明"的判定就是：页头区域的像素与 ``bg`` 完全一致。

两条证据：
  1. **逐像素**：把 CaptionBar / BrandHeader 单独渲染进一张预先填了 ``bg`` 的
     画布（``DrawChildren``，不铺窗底色），空白区的**每一个**像素都必须与 bg
     完全一致。两套主题 × 两种材质 × 强度 100 全查一遍 —— 透明是"什么都没画"，
     任何材质参数下都该一模一样。
  2. **真实窗口**：主窗口开到**液态玻璃 + 强度 100**（材质最强档，页头若还留着
     材质底会在这档最显眼），抓一张页头横幅存 ``_check/chrome_transparent.png``，
     人工目视 + 像素抽样双保险。

用法：
    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools\_probe_chrome_transparent.py
"""
import os
import sys

# 脚本在 devtools/ 下，运行时把项目根补进 sys.path（和其它探针一致）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

import time

from PySide6.QtCore import QPoint, QRect, QSettings
from PySide6.QtGui import QImage, QRegion
from PySide6.QtWidgets import QApplication, QWidget

OUT = os.path.join(_ROOT, "_check")

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    return ok


def pump(app, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def region_is_bg(canvas: QImage, x0: float, x1: float, y0: float, y1: float,
                 bg, dpr: float) -> tuple[bool, tuple | None]:
    """矩形区域（逻辑坐标）内每个像素都与 bg 完全一致？返回 (是否一致, 首个不一致者)。

    上界按画布尺寸夹住：越界采样会拿到无效颜色（#000000），那是假失败。
    """
    x1 = min(x1, canvas.width() / dpr)
    y1 = min(y1, canvas.height() / dpr)
    for y in range(int(y0 * dpr), int(y1 * dpr)):
        for x in range(int(x0 * dpr), int(x1 * dpr)):
            c = canvas.pixelColor(x, y)
            if (c.red(), c.green(), c.blue()) != (bg.red(), bg.green(), bg.blue()):
                return False, (x, y, c.name())
    return True, None


def check_standalone(scheme: str, material: str, strength: int) -> None:
    """控件单独渲染：空白区逐像素 == bg。"""
    from ui_brand import BrandHeader, CaptionBar
    from ui_theme import (
        CAPTION_H, apply_scheme, qcolor, set_glass_strength, set_material,
    )

    apply_scheme(scheme)
    set_material(material)
    set_glass_strength(strength)

    dpr = 2.0
    bg = qcolor("bg")
    label = f"[{scheme}/{material}/强度{strength}]"
    width = 900
    flags = QWidget.RenderFlag.DrawChildren   # 只跑 paintEvent + 子控件，不铺窗底色

    # ---- CaptionBar：整条中段（标题在左端、三键在右端，中段必然空白）----
    bar = CaptionBar("CQUFSAE 串口调试助手")
    bar.resize(width, CAPTION_H)
    canvas = QImage(int(width * dpr), int(CAPTION_H * dpr),
                    QImage.Format.Format_ARGB32_Premultiplied)
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(bg)
    bar.render(canvas, QPoint(0, 0), QRegion(), flags)
    ok, bad = region_is_bg(canvas, 440.0, 460.0, 0.0, float(CAPTION_H), bg, dpr)
    check(f"{label} 标题栏中段逐像素 == 背景色", ok,
          "" if ok else f"首个不一致像素 {bad}")
    bar.deleteLater()

    # ---- BrandHeader：队标（左）与产品名（右）之间的中段 ----
    header = BrandHeader("串口调试助手")
    header_h = header.sizeHint().height()
    header.resize(width, header_h)
    canvas = QImage(int(width * dpr), int(header_h * dpr),
                    QImage.Format.Format_ARGB32_Premultiplied)
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(bg)
    header.render(canvas, QPoint(0, 0), QRegion(), flags)
    # 中段（逻辑 x 690~710）落在队标右缘（≈24+560=584）与产品名左缘（≈774）之间，
    # 必然空白；底部细线在 height-1，y 上界让开 3px。
    ok, bad = region_is_bg(canvas, 690.0, 710.0, 0.0, header_h - 3, bg, dpr)
    check(f"{label} 品牌页头中段逐像素 == 背景色", ok,
          "" if ok else f"首个不一致像素 {bad}")
    header.deleteLater()


def real_window_band(app) -> None:
    """真实主窗口：液态玻璃 + 强度 100 下抓页头横幅，像素抽样 + 截图留档。"""
    from main_window import MainWindow
    from ui_theme import MATERIAL_LIQUID, qcolor

    window = MainWindow()
    window._port_watch.stop()
    window.setGeometry(0, 0, 1134, 820)
    window.show()
    pump(app, 1.2)

    # 最强材质档：液态玻璃 + 强度 100 —— 卡片在这档最"实"，页头若还留着材质底
    # 会在这里最显眼；像素仍等于 bg 才说明透明是真的。
    window._apply_theme("dark")
    window._apply_material(MATERIAL_LIQUID)
    window._apply_glass(100)
    pump(app, 0.5)

    # 横幅要盖住**整条页头**（含底部细线）：页头挂在内容容器里，与标题栏之间
    # 隔着布局间距（GAP - SHADOW_PAD*2），所以下界取页头实际底边，不能用
    # 两个控件高度直接相加。
    header = window.brand_header
    pos = header.mapTo(window, QPoint(0, 0))
    band_h = pos.y() + header.height()
    shot = window.grab(QRect(0, 0, window.width(), band_h)).toImage()
    # grab() 的 dpr 以窗口为准（toImage 在某些 Qt 版本上不继承 dpr，按尺寸核对）
    dpr = window.devicePixelRatioF()
    if shot.devicePixelRatio() and shot.width() == int(window.width() * shot.devicePixelRatio()):
        dpr = shot.devicePixelRatio()
    bg = qcolor("bg")

    # ---- 像素抽样：标题栏中段（x 400~700，避开左端标题与右端三键）----
    ok, bad = region_is_bg(shot, 400.0, 700.0, 0.0, float(window._caption_bar.height()),
                           bg, dpr)
    check("[真实窗口 liquid/100] 标题栏中段逐像素 == 背景色", ok,
          "" if ok else f"首个不一致像素 {bad}")

    # ---- 像素抽样：页头中段（队标右缘 ≈596 与产品名左缘 ≈990 之间；
    #      底部细线在最后 1px，y 上界让开 2px）----
    ok, bad = region_is_bg(shot, 650.0, 950.0, float(pos.y()),
                           float(pos.y() + header.height() - 2), bg, dpr)
    check("[真实窗口 liquid/100] 品牌页头中段逐像素 == 背景色", ok,
          "" if ok else f"首个不一致像素 {bad}")

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "chrome_transparent.png")
    shot.save(path, "PNG")
    print("截图 ->", path, f"（{shot.width()}x{shot.height()} 物理像素）")

    # 收尾按探针定式：关窗前把尺寸钉回来，QSettings 快照由调用方还原
    window.setGeometry(0, 0, 1134, 820)
    window.close()
    pump(app, 0.3)
    window.deleteLater()
    pump(app, 0.1)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    from ui_theme import ui_font
    app.setFont(ui_font(12.5))

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    # 窗口尺寸会从注册表恢复（上一次真实运行留下的）—— 钉死尺寸，否则按坐标
    # 取像素会漂（快照里有它，退出时原样还回去）。
    settings.remove("geometry")
    try:
        for scheme in ("dark", "light"):
            for material, strength in (("frosted", 100), ("liquid", 100)):
                check_standalone(scheme, material, strength)
        real_window_band(app)
    finally:
        for key in list(settings.allKeys()):
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            if value is not None:
                settings.setValue(key, value)
        settings.sync()

    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    print("\n" + "=" * 60)
    print(f"页头透明验证：{passed}/{total} 通过")
    for name, ok, _detail in _RESULTS:
        if not ok:
            print(f"  FAIL: {name}  {_detail}")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
