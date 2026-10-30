# -*- coding: utf-8 -*-
r"""两种材质 × 强度 30/100 的实拍 + 逐像素文字对比度核对。

四种组合各渲染一张卡片（毛玻璃/液态玻璃 × 强度 30/100），存到 ``_check/``；
再从**渲染出来的像素**里量「卡面底色 vs 卡面上的文字」的 WCAG 对比度，证明
材质强度拉满时文字仍然可读（苹果 §12「Vibrancy」那条的落地验收）。

顺带守一条回归：页头 / 标题栏**不铺材质**（用户点名要"背景透明的"，只有
卡片们保留毛玻璃 / 液态玻璃），四种组合下它们的渲染必须完全一致。

为什么用 ``QWidget.render()`` 而不是 ``grab()``：项目里 grab 在窗口缩放/全屏
切换期会返回全黑，且抓不到 QPlainTextEdit 的正文。``render()`` 是软件渲染进
QImage，结果稳定、可复现（自测里的边缘高光用例就是这么量的）。

用法：
    python devtools\_material_shots.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QPoint                              # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QRegion  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget      # noqa: E402

OUT = os.path.join(_ROOT, "_check")

# 卡片尺寸与内缩（和内边距 / 圆角无关的量都从 ui_theme 拿，别写死）
CARD_W, CARD_H = 380, 210


def relative_luminance(color: QColor) -> float:
    """WCAG 相对亮度。"""
    def channel(value: float) -> float:
        value = value / 255.0
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    return (0.2126 * channel(color.red()) + 0.7152 * channel(color.green())
            + 0.0722 * channel(color.blue()))


def contrast_ratio(a: QColor, b: QColor) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    light, dark = (la, lb) if la >= lb else (lb, la)
    return (light + 0.05) / (dark + 0.05)


def render_case(material: str, strength: int, scheme: str) -> tuple[QImage, dict]:
    """渲染一张卡片，返回 (图, 测量结果)。"""
    from ui_surface import Card
    from ui_theme import (
        qcolor, set_glass_strength, set_material, set_scheme, ui_font,
    )

    set_scheme(scheme)
    set_material(material)
    set_glass_strength(strength)

    holder = QWidget()
    card = Card("接收区", holder)
    card.resize(CARD_W, CARD_H)
    # 卡面上放两行文字：主文字（ink）与次级文字（ink_2）—— 苹果 §12 要的是
    # "毛玻璃/玻璃面上的文字用更高对比 + 略重字重"，两档都要量。
    body = card.body
    primary = QLabel("CQUFSAE 接收区  电压 3700 mV")
    primary.setObjectName("title")            # QSS 里 #title 用 ink + 600
    primary.setFont(ui_font(13.0, QFont.Weight.DemiBold))
    secondary = QLabel("第 12 轮 · Temperature1 · 22 ℃")
    secondary.setFont(ui_font(11.0))
    body.addWidget(primary)
    body.addWidget(secondary)
    card.layout().activate()
    for _ in range(3):
        QApplication.processEvents()

    dpr = 2.0
    canvas = QImage(int(CARD_W * dpr), int(CARD_H * dpr),
                    QImage.Format.Format_ARGB32_Premultiplied)
    # QImage 按物理像素建、再 setDevicePixelRatio，render 才按 2x 画进去（
    # 与自测里"角上 alpha"那两条的坑一致）。
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(qcolor("bg"))
    card.render(canvas)

    surface = _surface_color(canvas)
    primary_color = _text_color(canvas, primary, card, dpr)
    secondary_color = _text_color(canvas, secondary, card, dpr)
    result = {
        "scheme": scheme,
        "material": material,
        "strength": strength,
        "surface": surface.name(),
        "primary_text": primary_color.name(),
        "secondary_text": secondary_color.name(),
        "primary_contrast": contrast_ratio(surface, primary_color),
        "secondary_contrast": contrast_ratio(surface, secondary_color),
    }
    return canvas, result


def _interior_points(image: QImage, skip: int = 10):
    """遍历卡面内部像素（跳过最外圈 skip 与顶部标题带）。"""
    for y in range(skip, image.height() - skip, 2):
        for x in range(skip, image.width() - skip, 2):
            yield x, y, image.pixelColor(x, y)


def _surface_color(image: QImage) -> QColor:
    """卡面底色：取卡面**下半部**的中位亮度像素（避开标题/正文墨迹与描边）。"""
    from ui_theme import CARD_PAD, SHADOW_PAD
    dpr = image.devicePixelRatio() or 2.0
    x0 = int((SHADOW_PAD + CARD_PAD) * dpr)
    x1 = image.width() - x0
    y0 = int(image.height() * 0.72)
    y1 = image.height() - int((SHADOW_PAD + 4) * dpr)
    samples = [image.pixelColor(x, y)
               for y in range(y0, y1, 2) for x in range(x0, x1, 2)]
    samples.sort(key=relative_luminance)
    return samples[len(samples) // 2] if samples else QColor(0, 0, 0)


def _text_color(image: QImage, label: QLabel, card, dpr: float) -> QColor:
    """标签文字色：在标签矩形内取"与卡面底色对比度最大"的那个像素。

    不取最亮像素 —— 浅色主题下主文字是近**黑**，取最亮会拿到背景。用对比度最大
    的那个像素对两种主题都成立。
    """
    top_left = label.mapTo(card, QPoint(0, 0))
    rect_x = int(top_left.x() * dpr)
    rect_y = int(top_left.y() * dpr)
    rect_w = int(label.width() * dpr)
    rect_h = int(label.height() * dpr)
    surface = _surface_color(image)
    best, best_ratio = surface, 0.0
    for y in range(rect_y, min(rect_y + rect_h, image.height()), 1):
        for x in range(rect_x, min(rect_x + rect_w, image.width()), 1):
            color = image.pixelColor(x, y)
            ratio = contrast_ratio(surface, color)
            if ratio > best_ratio:
                best, best_ratio = color, ratio
    return best


def render_chrome(material: str, strength: int, scheme: str) -> QImage:
    """把自绘标题栏 + 品牌页头渲染成一张图。

    页头 / 标题栏**不画材质底**（用户要过"背景透明的"，见 ``ui_brand`` 的
    说明）—— 这张图现在只是留档：它们在所有材质 / 强度下都应当长得一样
    （直接坐在页面背景上），差异恒为 0。
    """
    from ui_brand import BrandHeader, CaptionBar
    from ui_theme import (
        CAPTION_H, qcolor, set_glass_strength, set_material, set_scheme,
    )

    set_scheme(scheme)
    set_material(material)
    set_glass_strength(strength)

    width = 900
    bar = CaptionBar("CQUFSAE 串口助手")
    bar.resize(width, CAPTION_H)
    header = BrandHeader("串口助手")
    header_h = header.sizeHint().height()
    header.resize(width, header_h)
    gap = 10
    height = CAPTION_H + gap + header_h

    dpr = 2.0
    canvas = QImage(int(width * dpr), int(height * dpr),
                    QImage.Format.Format_ARGB32_Premultiplied)
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(qcolor("bg"))
    flags = QWidget.RenderFlag.DrawChildren   # 只跑 paintEvent + 子控件，不铺窗底色
    bar.render(canvas, QPoint(0, 0), QRegion(), flags)
    header.render(canvas, QPoint(0, CAPTION_H + gap), QRegion(), flags)
    return canvas


def mean_abs_diff(a: QImage, b: QImage) -> float:
    """两张同尺寸图的平均绝对差（0~255）。

    页头 / 标题栏透明化之后这里反过来用：它们的渲染**不随材质 / 强度变**，
    所以四种组合两两之间应当恒为 0 —— 非 0 就说明页头又被铺上了材质底
    （用户明确要过"这里背景透明的"）。
    """
    if a.size() != b.size():
        return 255.0
    total = count = 0
    for y in range(0, a.height(), 2):
        for x in range(0, a.width(), 2):
            ca, cb = a.pixelColor(x, y), b.pixelColor(x, y)
            total += (abs(ca.red() - cb.red()) + abs(ca.green() - cb.green())
                      + abs(ca.blue() - cb.blue())) / 3.0
            count += 1
    return total / count if count else 0.0


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, set_glass_strength, set_material, ui_font

    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    from ui_theme import current_scheme

    cases = [("frosted", 30), ("frosted", 100), ("liquid", 30), ("liquid", 100)]
    shots: list[tuple[str, QImage]] = []
    results: list[dict] = []
    for material, strength in cases:
        canvas, result = render_case(material, strength, "dark")
        name = f"material_{material}_{strength}.png"
        path = os.path.join(OUT, name)
        canvas.save(path, "PNG")
        shots.append((f"{material} {strength}", canvas))
        results.append(result)
        print(f"[{material:7s} 强度{strength:3d}] 卡面={result['surface']} "
              f"主文字={result['primary_text']} 对比={result['primary_contrast']:.2f}:1 | "
              f"次文字={result['secondary_text']} 对比={result['secondary_contrast']:.2f}:1")

    # 拼一张对照图，一眼看两种材质 / 两档强度
    pad, label_h = 16, 26
    sheet = QImage(pad * (len(shots) + 1) + CARD_W * 2 * len(shots),
                   label_h + CARD_H * 2 + pad * 2, QImage.Format.Format_ARGB32)
    sheet.fill(QColor(current_scheme() == "dark" and "#0D0D0F" or "#FFFFFF"))
    painter = QPainter(sheet)
    painter.setFont(QFont("Microsoft YaHei UI", 11))
    for i, (title, img) in enumerate(shots):
        x = pad + i * (CARD_W * 2 + pad)
        painter.setPen(QColor("#F2F3F5") if current_scheme() == "dark"
                       else QColor("#111214"))
        painter.drawText(x, label_h - 6, title)
        painter.drawImage(x, label_h, img)
    painter.end()
    sheet_path = os.path.join(OUT, "material_compare.png")
    sheet.save(sheet_path, "PNG")
    print("\n对照图:", sheet_path)

    # ---- chrome（自绘标题栏 + 品牌页头）：透明，不随材质 / 强度变 ----
    # 页头 / 标题栏直接坐在页面背景上（用户要过"这里背景透明的"），所以四种
    # 材质 / 强度组合渲染出来必须**一模一样**（差异恒为 0）。这同时是一条回归
    # 守卫：哪天又有人给页头铺上材质底，这里立刻非 0 挂出来。
    chrome: dict[tuple[str, int], QImage] = {}
    for material, strength in cases:
        image = render_chrome(material, strength, "dark")
        chrome[(material, strength)] = image
        image.save(os.path.join(OUT, f"chrome_{material}_{strength}.png"), "PNG")

    d_material = mean_abs_diff(chrome[("frosted", 100)], chrome[("liquid", 100)])
    d_strength_f = mean_abs_diff(chrome[("frosted", 30)], chrome[("frosted", 100)])
    d_strength_l = mean_abs_diff(chrome[("liquid", 30)], chrome[("liquid", 100)])
    print("\nchrome（标题栏+页头）外观差异（平均绝对差 / 255，透明页头应恒为 0）：")
    print(f"  材质 frosted→liquid @强度100 : {d_material:.2f}")
    print(f"  强度 30→100 @毛玻璃          : {d_strength_f:.2f}")
    print(f"  强度 30→100 @液态玻璃        : {d_strength_l:.2f}")

    ch_h = chrome[("frosted", 30)].height()
    ch_w = chrome[("frosted", 30)].width()
    ch_sheet = QImage(pad * (len(cases) + 1) + ch_w * len(cases),
                      label_h + ch_h + pad, QImage.Format.Format_ARGB32)
    ch_sheet.fill(QColor("#0D0D0F"))
    ch_painter = QPainter(ch_sheet)
    ch_painter.setFont(QFont("Microsoft YaHei UI", 11))
    ch_painter.setPen(QColor("#F2F3F5"))
    for i, (material, strength) in enumerate(cases):
        x = pad + i * (ch_w + pad)
        ch_painter.drawText(x, label_h - 6, f"{material} {strength}")
        ch_painter.drawImage(x, label_h, chrome[(material, strength)])
    ch_painter.end()
    ch_path = os.path.join(OUT, "chrome_compare.png")
    ch_sheet.save(ch_path, "PNG")
    print("chrome 对照图:", ch_path)

    # ---- 判定：两种材质、强度 100（材质拉满）时文字仍要可读 ----
    ok = True
    for r in results:
        if r["primary_contrast"] < 4.5:
            ok = False
            print(f"FAIL: {r['material']} {r['strength']} 主文字对比不足 "
                  f"{r['primary_contrast']:.2f}:1")
        if r["secondary_contrast"] < 3.0:
            ok = False
            print(f"FAIL: {r['material']} {r['strength']} 次文字对比不足 "
                  f"{r['secondary_contrast']:.2f}:1")
    print("\n文字可读性判定:", "PASS（主文字≥4.5:1 且 次文字≥3.0:1）"
          if ok else "FAIL")

    # ---- 判定：页头 / 标题栏透明 —— 四种组合两两之间零差异 ----
    chrome_ok = max(d_material, d_strength_f, d_strength_l) == 0.0
    if not chrome_ok:
        print("FAIL: 页头/标题栏渲染随材质/强度变了 —— 它们应当透明、不铺材质底"
              "（用户：这里背景为什么要改，给我改回去，透明的）")
    print("页头透明判定:", "PASS（各材质/强度下渲染完全一致）" if chrome_ok else "FAIL")

    set_glass_strength(0)
    set_material("frosted")
    return 0 if ok and chrome_ok else 1


if __name__ == "__main__":
    sys.exit(main())
