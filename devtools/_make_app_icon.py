# -*- coding: utf-8 -*-
"""从品牌 SVG 生成多尺寸 ``assets/app.ico``（窗口图标 + 桌面快捷方式图标）。

为什么要有这个脚本：``app.ico`` 是**派生资产**，源头是 ``assets/*.svg`` 的矢量队标。
以前靠手工渲染，改一次颜色就得重新猜一遍圆角/渐变的数值 —— 现在数值全落在下面的
常量里，换标只换 ``--svg`` 一个参数。

几何对齐 2026-10-12 那版（用户只提出「闪电要变红」，其余一律不许漂）：
  · 画布满幅圆角矩形，半径 18.55%（256 上实测 47.5px）
  · 竖向线性渐变 #1C1D20 → #111214
  · 顶部 1px 高光（白 13%）
  · 墨迹宽度占画布 88.28%，**墨迹盒**（不是 viewBox）居中

画质做法（用户 2026-11-06「画质要超级高」）：
  · 每一档独立矢量渲染 + 4~16 倍超采样 + LANCZOS 降采样（不是一份母版硬缩）
  · 瓦片与队标**分两层**渲染，队标层单独降采样，才能对细笔画单独做处理
  · 小尺寸光学补偿：墨迹每档按比例撑大（16px 到 95.5%），否则 8 个字母挤进
    15px 全是灰雾；≤40px 再对队标 alpha 做一次 gamma 收紧（把被抗锯齿洗淡的
    笔画压回实色），这是让小图标「看着锐」的关键
  · 另出 1024 母版 PNG，给文档/发布页/安装器用（Windows 图标上限就是 256）

用法：
    python devtools/_make_app_icon.py                      # 默认队标 → assets/app.ico
    python devtools/_make_app_icon.py --svg X.svg --out Y.ico
    python devtools/_make_app_icon.py --master 1024        # 顺便导母版 PNG
    python devtools/_make_app_icon.py --preview 256        # 只导一张 PNG 看效果
"""

from __future__ import annotations

import io
import os
import struct
import sys

from PIL import Image
from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SVG = os.path.join(_ROOT, "assets", "mark_mono_white.svg")
DEFAULT_OUT = os.path.join(_ROOT, "assets", "app.ico")
DEFAULT_MASTER = os.path.join(_ROOT, "docs", "images", "app_icon_1024.png")

# ---- 与原图标逐像素对齐的设计常量 ------------------------------------------
TILE_RADIUS = 0.1855          # 圆角半径 / 画布边长（256 实测 47.5 → 0.1855）
GRAD_TOP = (28, 29, 32)       # #1C1D20
GRAD_BOTTOM = (17, 18, 20)    # #111214
RIM_ALPHA = 0.13              # 顶部 1px 高光的白透明度
INK_W = 0.8828                # 大尺寸的墨迹宽度 / 画布边长（实测 226/256）

# 小尺寸光学补偿：同样的 88.28% 在 16px 上只剩 14px 宽、8 个字母挤成一团灰。
# 越小的档位墨迹撑得越多 —— 这是「同一枚标在不同尺寸下看着一样重」的标准做法。
INK_BY_SIZE = {16: 0.954, 20: 0.947, 24: 0.940, 32: 0.929,
               40: 0.920, 48: 0.912, 60: 0.901, 64: 0.898, 72: 0.893, 96: 0.888}

# ≤40px 的 alpha gamma 收紧：降采样会把细笔画洗成 40~80% 的灰，gamma<1 把它们
# 拉回接近实色，笔画的「黑度」够了，眼睛才觉得锐。值是 gamma 指数。
# ⚠️ 别压太狠（0.58 那种）：16px 上会糊成一团白块，反而更脏。
ALPHA_GAMMA = {16: 0.70, 20: 0.74, 24: 0.78, 32: 0.84, 40: 0.89}

# Explorer / 任务栏 / Alt-Tab 会按 DPI 挑最近的一档，14 档是原来的全集
SIZES = (16, 20, 24, 32, 40, 48, 60, 64, 72, 96, 128, 160, 192, 256)


def _renderer(path: str):
    from PySide6.QtSvg import QSvgRenderer
    with open(path, "rb") as fh:
        data = fh.read()
    renderer = QSvgRenderer(QByteArray(data))
    if not renderer.isValid():
        raise SystemExit(f"FAIL 无效 SVG: {path}")
    return renderer


def _to_pil(img: QImage) -> Image.Image:
    """QImage → PIL，按 bytesPerLine 处理行对齐，格式统一成 RGBA。"""
    img = img.convertToFormat(QImage.Format_RGBA8888)
    stride = img.bytesPerLine()
    raw = bytes(img.constBits())
    return Image.frombuffer("RGBA", (img.width(), img.height()), raw,
                            "raw", "RGBA", stride, 1).copy()


def _ink_bbox(renderer, probe: int = 1024) -> tuple[float, float, float, float]:
    """在 SVG 用户坐标里量出真实墨迹盒（含抗锯齿外扩 ~1px）。

    不能直接按 viewBox 居中：队标四周留白不均（x 6~1011 / y 11~393），按 viewBox
    摆会让字整体偏下、偏小 —— 这是「图标看着不居中」的经典来源。
    """
    vb = renderer.viewBoxF()
    pad = probe * 0.04
    scale = (probe - 2 * pad) / vb.width()
    img = QImage(probe, probe, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.translate(pad, pad)
    painter.scale(scale, scale)
    renderer.render(painter, QRectF(0, 0, vb.width(), vb.height()))
    painter.end()

    box = _to_pil(img).getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if box is None:
        raise SystemExit("FAIL 这个 SVG 渲染出来是空的")
    x0, y0, x1, y1 = box
    to_svg = lambda px: (px - pad) / scale  # noqa: E731
    return (to_svg(x0), to_svg(y0), to_svg(x1), to_svg(y1))


def _supersample(size: int) -> int:
    """超采样倍数：小图标狠狠超采样（16px→256 倍面积代价可忽略），大档封顶 2048px。"""
    return max(4, min(16, 2048 // size))


# 8×8 Bayer 有序抖动矩阵（0~63）。暗底渐变只有 ~11 级灰阶，256 档上每级要横跨
# 20~25px、母版上 100px —— 眼睛直接看出「台阶」。加 ±0.5 LSB 抖动把台阶打散成
# 噪点，肉眼看到的是连续过渡。有序抖动比随机噪声干净：不会在 1:1 下出现脏颗粒。
_BAYER8 = (
    (0, 32, 8, 40, 2, 34, 10, 42),
    (48, 16, 56, 24, 50, 18, 58, 26),
    (12, 44, 4, 36, 14, 46, 6, 38),
    (60, 28, 52, 20, 62, 30, 54, 22),
    (3, 35, 11, 43, 1, 33, 9, 41),
    (51, 19, 59, 27, 49, 17, 57, 25),
    (15, 47, 7, 39, 13, 45, 5, 37),
    (63, 31, 55, 23, 61, 29, 53, 21),
)


def _gradient_rgb(size: int) -> Image.Image:
    """竖向渐变 + 有序抖动（逐像素浮点算，别让 Qt 在 8bit 上直接插值出色带）。"""
    import numpy as np
    t = (np.arange(size, dtype=np.float64) + 0.5) / size
    top = np.array(GRAD_TOP, dtype=np.float64)
    bottom = np.array(GRAD_BOTTOM, dtype=np.float64)
    ramp = top[None, :] + (bottom - top)[None, :] * t[:, None]        # (size, 3)
    bayer = np.array(_BAYER8, dtype=np.float64) / 64.0
    reps = size // 8 + 1
    pattern = np.tile(bayer, (reps, reps))[:size, :size]              # (size, size)
    dither = (pattern - 0.5)[:, :, None]                               # ±0.5 LSB
    rgb = np.clip(np.floor(ramp[:, None, :] + dither + 0.5), 0, 255)
    return Image.fromarray(rgb.astype(np.uint8), "RGB")


def _render_layers(size: int, renderer, ink_ratio: float
                   ) -> tuple[Image.Image, Image.Image]:
    """渲染 size×size 的「瓦片层」和「队标层」（都返回成品尺寸的 PIL 图）。"""
    ss = _supersample(size)
    big = size * ss
    vb = renderer.viewBoxF()
    ix0, iy0, ix1, iy1 = _ink_bbox(renderer)
    ink_w, ink_h = ix1 - ix0, iy1 - iy0

    # --- 瓦片层：圆角矩形的「蒙版」由 Qt 出（AA 最好），颜色用抖动渐变自己填
    mask_img = QImage(big, big, QImage.Format_ARGB32)
    mask_img.fill(Qt.transparent)
    p = QPainter(mask_img)
    p.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    inset = 0.5 * ss
    path.addRoundedRect(QRectF(inset, inset, big - 2 * inset, big - 2 * inset),
                        big * TILE_RADIUS, big * TILE_RADIUS)
    p.fillPath(path, QColor(255, 255, 255))
    p.end()
    mask = _to_pil(mask_img).getchannel("A").resize((size, size), Image.LANCZOS)
    tile = Image.merge("RGBA", (*_gradient_rgb(size).split(), mask))

    # --- 队标层：单独一张透明图，墨迹盒缩放到 ink_ratio，墨迹盒中心对齐画布中心
    art = QImage(big, big, QImage.Format_ARGB32)
    art.fill(Qt.transparent)
    p = QPainter(art)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    s = (big * ink_ratio) / ink_w
    renderer.render(p, QRectF(
        (big - ink_w * s) / 2 - ix0 * s,
        (big - ink_h * s) / 2 - iy0 * s,
        vb.width() * s,
        vb.height() * s,
    ))
    p.end()

    return tile, _to_pil(art).resize((size, size), Image.LANCZOS)


def _boost_alpha(art: Image.Image, gamma: float) -> Image.Image:
    """把队标层被抗锯齿洗淡的 alpha 收紧（gamma<1 → 半透明像素变实）。"""
    lut = [round(255 * (i / 255) ** gamma) for i in range(256)]
    r, g, b, a = art.split()
    return Image.merge("RGBA", (r, g, b, a.point(lut)))


def _render_icon(size: int, renderer) -> Image.Image:
    ink = INK_BY_SIZE.get(size, INK_W)
    tile, art = _render_layers(size, renderer, ink)
    gamma = ALPHA_GAMMA.get(size)
    if gamma:
        art = _boost_alpha(art, gamma)
    out = Image.alpha_composite(tile, art)
    return _apply_top_rim(out)


def _apply_top_rim(im: Image.Image) -> Image.Image:
    """在成品尺寸上贴 1px 顶部高光：逐列找第一个「实心」像素行，混白 13%。

    逐列而不是整行画一条直线 —— 圆角处顶边是斜的，逐列才能贴着弧线走，
    也不会在拐角外溢到透明区（那会在浅色桌面上露出一小截白边）。
    """
    px = im.load()
    w, h = im.size
    for x in range(w):
        for y in range(h):
            a = px[x, y][3]
            if a < 230:
                continue
            r, g, b, a = px[x, y]
            k = RIM_ALPHA
            px[x, y] = (round(r + (255 - r) * k), round(g + (255 - g) * k),
                        round(b + (255 - b) * k), a)
            break
    return im


def _png_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _save_ico(images: dict[int, Image.Image], out: str) -> None:
    """手写 ICO 容器：每档都是 PNG（与旧版一致，Win10/11 直接吃）。"""
    entries = [(size, _png_bytes(images[size])) for size in sorted(images)]
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory, payload = b"", b""
    for size, png in entries:
        directory += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                                 1, 32, len(png), offset)
        offset += len(png)
        payload += png
    with open(out, "wb") as fh:
        fh.write(header + directory + payload)


def main(argv: list[str]) -> int:
    svg, out, preview = DEFAULT_SVG, DEFAULT_OUT, 0
    master = 0
    args = argv[1:]
    while args:
        flag = args.pop(0)
        if flag == "--svg":
            svg = args.pop(0)
        elif flag == "--out":
            out = args.pop(0)
        elif flag == "--master":
            master = int(args.pop(0)) if args and args[0].isdigit() else 1024
        elif flag == "--preview":
            preview = int(args.pop(0)) if args and args[0].isdigit() else 256
        else:
            print(f"unknown arg: {flag}", file=sys.stderr)
            return 2

    app = QGuiApplication(argv)  # noqa: F841  QImage/QPainter 需要
    renderer = _renderer(svg)

    if preview:
        image = _render_icon(preview, renderer)
        path = os.path.splitext(out)[0] + f"_{preview}.png"
        image.save(path)
        print(f"  preview -> {path}")
        return 0

    images = {size: _render_icon(size, renderer) for size in SIZES}
    _save_ico(images, out)
    print(f"  {len(images)} 档 -> {out}  ({os.path.getsize(out)} bytes)")

    if master:
        big = _render_icon(master, renderer)
        big.save(DEFAULT_MASTER)
        print(f"  母版 -> {DEFAULT_MASTER}  {big.size[0]}x{big.size[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
