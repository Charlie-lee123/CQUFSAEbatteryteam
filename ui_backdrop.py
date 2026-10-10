"""页面背景与磨砂颗粒。

**页面背景是纯色**，两个主题都只填 ``ui_theme`` 里的 ``bg`` 一个 token。
这里曾经按主题叠过三层：对角渐变、柔光斑、间距 26px 的细网格 —— 三层被用户逐个否掉
（浅色渐变"太丑"、深色网格被读成"方格"、最后深色渐变也被点名"背景不要渐变，要纯色"）。
既然没有一个主题再有背景纹理，那套"按配方叠层 + 卡片裁一层虚化背景贴上来"的机制
就一并删掉了 —— 留着一套永远不生效的开关，下次只会有人把它打开。

**磨砂感两个主题走同一条路：玻璃自身表面的散射颗粒**（``frost_tile`` +
``ui_theme.FROST_GRAIN``）。这不是退而求其次 —— 磨砂玻璃真正的物理特征本来就是
表面微结构把透过的光打散。一张纯色背景糊上十遍仍是那张纯色，所以"把背景虚化"这条路
在纯色页面下本来就走不通，而颗粒是直接画在玻璃面上的、看得见。

高斯模糊本身仍留在这里：卡片的柔和阴影要用同一份实现，避免两处走样。
"""

from __future__ import annotations

import random

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene,
)

from ui_theme import FROST_GRAIN, current_scheme, scheme_qcolor


# ---------------------------------------------------------------- 高斯模糊

_blur_scene: QGraphicsScene | None = None
_blur_item: QGraphicsPixmapItem | None = None
_blur_effect: QGraphicsBlurEffect | None = None


def _ensure_blur():
    """复用同一套 scene/item/effect，不每次新建。"""
    global _blur_scene, _blur_item, _blur_effect
    if _blur_scene is None:
        _blur_effect = QGraphicsBlurEffect()
        _blur_effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
        _blur_item = QGraphicsPixmapItem()
        _blur_item.setGraphicsEffect(_blur_effect)
        _blur_scene = QGraphicsScene()
        _blur_scene.addItem(_blur_item)
    return _blur_scene, _blur_item, _blur_effect


def gaussian_blur(source: QImage, radius: int) -> QImage:
    """真高斯模糊，返回与原图同尺寸的结果。

    四周要留内边距再裁回来：不留边的话模糊会把画布外当透明，
    结果边缘一圈发暗，贴出来就是一道灰边。
    """
    scene, item, effect = _ensure_blur()
    pad = max(2, radius * 2)
    width, height = source.width(), source.height()
    canvas = QImage(width + pad * 2, height + pad * 2,
                    QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawImage(pad, pad, source)
    painter.end()

    item.setPixmap(QPixmap.fromImage(canvas))
    effect.setBlurRadius(float(radius))
    scene.setSceneRect(0.0, 0.0, float(canvas.width()), float(canvas.height()))

    out = QImage(canvas.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    scene.render(painter)
    painter.end()
    return out.copy(pad, pad, width, height)


# ---------------------------------------------------------------- 磨砂颗粒

_frost_tiles: dict[tuple[str, int], QPixmap] = {}


def _blur_seamless(tile: QImage, radius: int) -> QImage:
    """把贴图先铺成 3×3 再整体模糊、最后裁回中心那一块。

    为什么不能直接糊：模糊会让画布外的部分按"透明"参与运算，贴图四周必然发暗，
    平铺出去就是一格一格的淡边（比颗粒本身还显眼）。铺成 3×3 之后，接缝两侧
    采样到的都是真实存在的邻居像素，中心那块于是和四周完全接得上。
    """
    side = tile.width()
    canvas = QImage(side * 3, side * 3, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawTiledPixmap(canvas.rect(), QPixmap.fromImage(tile))
    painter.end()
    return gaussian_blur(canvas, radius).copy(side, side, side, side)


def frost_tile(scheme: str | None = None, dpr: float = 1.0) -> QPixmap | None:
    """磨砂颗粒贴图：可平铺的 1 物理像素级随机噪点。该主题不铺颗粒时返回 ``None``。

    四件事值得说明：
    - **固定种子**。颗粒每次运行都长一样。界面不该每帧换一副噪点，那样看着像雪花屏。
    - **按物理像素生成**（边长 = tile × dpr 并设好 devicePixelRatio）。直接按逻辑像素
      生成的话，2 倍屏上每颗颗粒会被拉成 2×2 物理像素，成了粗麻点而不是细磨砂。
    - **生成后一定糊一道**（``_blur_seamless``，半径见 ``FROST_GRAIN[..]["blur"]``）。
      硬噪点是"砂纸"，糊过的才是"雾" —— 这一道决定了它看着像磨砂玻璃还是像粗纸。
    - 只生成**最大幅度**的贴图，实际幅度由调用方 ``painter.setOpacity()`` 缩放 ——
      否则滑杆每动一格都得重算一张噪点，拖动会卡。
    """
    scheme = scheme or current_scheme()
    spec = FROST_GRAIN.get(scheme)
    if not spec or spec["amp"][1] <= 0:
        return None

    dpr = max(1.0, float(dpr))
    key = (scheme, int(round(dpr * 100)))
    cached = _frost_tiles.get(key)
    if cached is not None:
        return cached

    side = max(8, int(spec["tile"]))
    px = max(8, int(round(side * dpr)))
    top = max(1, int(spec["amp"][1]))
    blur = max(0, int(round(float(spec.get("blur", 0.0)) * dpr)))
    color = QColor(spec["color"])
    red, green, blue = color.red(), color.green(), color.blue()
    rng = random.Random(0xC0FFEE)

    # 直接拼 ARGB32 字节（内存序 B,G,R,A）。比逐像素 setPixelColor 快一个量级，
    # 这张图虽然只算一次，但一算就是十几万像素，省下来的是启动时的卡顿。
    buf = bytearray(px * px * 4)
    for i in range(px * px):
        j = i * 4
        buf[j] = blue
        buf[j + 1] = green
        buf[j + 2] = red
        buf[j + 3] = rng.randint(0, top)
    image = QImage(bytes(buf), px, px, px * 4, QImage.Format.Format_ARGB32).copy()

    if blur > 0:
        image = _blur_seamless(image, blur)

    tile = QPixmap.fromImage(image)
    tile.setDevicePixelRatio(dpr)          # fromImage 不保留 DPR，必须显式设回
    _frost_tiles[key] = tile
    return tile


# ---------------------------------------------------------------- 页面背景

def paint_backdrop(painter: QPainter, rect: QRect, scheme: str | None = None) -> None:
    """把页面底色铺满 ``rect``：**纯色**，主题不同只是底色不同。

    写成"往一个 painter 上填一块"而不是"生成一张底图"，是因为纯色没有缓存价值 ——
    直接 ``fillRect`` 比每次绘制都去贴一张整窗大小的位图更省，也不再需要按尺寸失效缓存。

    主题**必须走参数**：换主题的那一瞬间主窗口就会带着新主题重绘，
    在自动重绘的时序里读全局有可能拿到上一套颜色。
    """
    painter.fillRect(rect, scheme_qcolor("bg", scheme or current_scheme()))
