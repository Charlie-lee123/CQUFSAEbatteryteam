"""平面卡片、毛玻璃与柔和阴影。

两件事：
1. **毛玻璃**：半透明面色 + **糊过的**散射颗粒（``_paint_frost``）+ 上缘高光。
   页面背景是纯色，身后没有可采样的纹理，所以磨砂完全由玻璃自身承担 ——
   铺颗粒而不是糊背景，理由见 ``ui_backdrop`` 的模块说明；颗粒必须先糊一道，
   否则那是粗纸，理由见 ``ui_theme.FROST_GRAIN``。
2. **柔和阴影**：QSS 没有 box-shadow，而 ``QGraphicsDropShadowEffect`` 会让控件
   常态走离屏渲染 —— 串口一来数据就要重绘，这个代价不能接受。所以自己画：
   把圆角矩形的剪影模糊一次、按尺寸缓存，之后每次只是贴一张位图。

高斯模糊的实现放在 ``ui_backdrop``，这里共用同一份，避免两处走样。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush, QColor, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QVBoxLayout, QWidget,
)

from ui_anim import Tween, out_cubic
from ui_backdrop import frost_tile, gaussian_blur
from ui_theme import (
    CARD_PAD, CARD_RADIUS, SHADOW_BLUR, SHADOW_PAD, TITLE_H, current_scheme,
    frost_gain, glass_alpha, mix, qcolor, shadow_alpha, ui_font,
)

# ---------------------------------------------------------------- 高斯模糊

# 高斯模糊实现见 ui_backdrop.gaussian_blur（卡片阴影与背景虚化共用同一份）

# 玻璃上缘高光。RIM_ALPHA 是最大不透明度（再乘磨砂强度），RIM_FADE 是它往下渐隐到
# 消失的位置（占卡片高度的比例）。太亮会变成"描了道白边"，太暗则完全看不出玻璃板 ——
# 这两个值是在 2 倍屏上目视定的，不是算出来的。
RIM_ALPHA = 84
RIM_FADE = 0.6


# ---------------------------------------------------------------- 阴影缓存

_shadows: dict[tuple[int, int, int, int], QPixmap] = {}
_SHADOW_CACHE_LIMIT = 32


def paint_surface(painter: QPainter, box: QRectF, radius: float,
                  fill: QColor | None = None,
                  border: QColor | None = None) -> QPainterPath:
    """画一块「圆角实面 + 1px 描边」，返回这条圆角路径。

    ⛔ 全 App 的"面"都要这样画，**别用 QSS 的 background/border-radius 画面**：
    QSS 对**普通 QWidget** 的 border-radius 在真实主窗口里会被整条丢掉
    （2026-10-14 实测：同一份样式表在隔离窗口里是圆的、在主窗口里却是直角，
    连强制 unpolish/polish 都救不回来），而 QPlainTextEdit / QLineEdit 那类
    原生控件才稳定 —— 一半圆一半方就是这么来的。卡片本来就是自绘的，
    统一走这条路才不会再踩。

    ``box`` 想要 1px 描边正好落在控件边界内的话，调用方自己先内缩 0.5。
    """
    path = QPainterPath()
    path.addRoundedRect(box, radius, radius)
    if fill is not None:
        painter.fillPath(path, fill)
    if border is not None:
        pen = QPen(border)
        pen.setWidthF(1.0)
        painter.strokePath(path, pen)
    return path


def card_shadow(width: int, height: int, radius: int, alpha: int) -> QPixmap:
    """卡片阴影：圆角剪影模糊一次后缓存。按 (宽, 高, 圆角, 透明度) 取。"""
    key = (width, height, radius, alpha)
    cached = _shadows.get(key)
    if cached is not None:
        return cached
    if len(_shadows) >= _SHADOW_CACHE_LIMIT:
        _shadows.clear()

    spread = SHADOW_BLUR
    image = QImage(width + spread * 2, height + spread * 2,
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    silhouette = QPainterPath()
    silhouette.addRoundedRect(QRectF(spread, spread, width, height), radius, radius)
    painter.fillPath(silhouette, QColor(0, 0, 0, alpha))
    painter.end()

    blurred = gaussian_blur(image, SHADOW_BLUR // 2)
    shadow = QPixmap.fromImage(blurred)
    _shadows[key] = shadow
    return shadow



# ---------------------------------------------------------------- 入场淡入

def fade_in(widget: QWidget, delay_ms: int = 0, duration_ms: int = 240) -> None:
    """入场淡入。小米那套动效是短、快、收得住，不做过冲也不做扫光。

    走 ``ui_anim`` 的 120fps 补间 —— 200ms 这种短过渡在 60fps 下只有十来帧，
    高刷屏上会觉得"一顿一顿"，帧率提上去才是连续的淡入。

    结束后必须摘掉透明度效果：留着会让控件常态走离屏合成。
    """
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)

    def finish() -> None:
        # 最后一帧还在效果里画，等下一轮事件循环再摘，免得闪一下。
        # 控件若在这 0ms 间隙里被销毁（窗口关闭），setGraphicsEffect 会踩已
        # 删除对象 —— 效果随宿主一起销毁了，什么都不用做。
        def remove_effect() -> None:
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
        QTimer.singleShot(0, remove_effect)

    # 补间挂在控件上，由控件持有它的生命周期
    tween = Tween(duration_ms, out_cubic, effect.setOpacity, finish, parent=widget)
    QTimer.singleShot(delay_ms, lambda: tween.start(0.0, 1.0))


# ---------------------------------------------------------------- 卡片

class Card(QFrame):
    """平面卡片：柔和阴影 + 圆角实底 + 标题行细线。

    自带一个 ``body`` 垂直布局，调用方往里塞内容即可，内边距由卡片统一给，
    免得每处各写一套。
    """

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._flash = 0.0
        self._header_buttons: list[QWidget] = []
        self._title_font = ui_font(13.5, ui_font().weight())
        # 描边闪一下用的补间：复用同一个，数据一密也不会叠出多个动画互相打架
        self._flash_anim = Tween(380, out_cubic, self._on_flash, parent=self)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        pad = SHADOW_PAD
        outer = QVBoxLayout(self)
        outer.setContentsMargins(pad + CARD_PAD, pad + TITLE_H + 10,
                                 pad + CARD_PAD, pad + CARD_PAD)
        outer.setSpacing(0)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body)

    # -- 对外 ------------------------------------------------------------

    def box(self) -> QRectF:
        """卡片可见矩形（去掉为阴影预留的空隙）。"""
        return QRectF(self.rect()).adjusted(SHADOW_PAD, SHADOW_PAD, -SHADOW_PAD, -SHADOW_PAD)

    def add_header_button(self, button: QWidget) -> None:
        """把按钮排在标题行右侧 —— Windows 那种把折叠按钮放进标题栏的做法。"""
        button.setParent(self)
        self._header_buttons.append(button)
        self._layout_header_buttons()

    def relayout_header(self) -> None:
        """标题栏按钮**自己改了尺寸**之后请卡片重排一次。

        卡片只在 ``resizeEvent`` 里自动重排，而"某个按钮变宽了"（比如接收区
        时钟在「带日期 / 不带日期」之间换格式）不会让卡片收到 resize ——
        不显式叫一声，那颗按钮就会拿着新宽度停在旧位置上。
        """
        self._layout_header_buttons()

    def _layout_header_buttons(self) -> None:
        if not self._header_buttons:
            return
        box = self.box()
        right = box.right() - CARD_PAD + 3
        center_y = box.top() + SHADOW_PAD + TITLE_H * 0.5
        for button in reversed(self._header_buttons):    # 从右往左依次排
            button.move(int(right - button.width()),
                        int(center_y - button.height() / 2.0))
            right -= button.width() + 4

    def resizeEvent(self, event) -> None:
        self._layout_header_buttons()
        super().resizeEvent(event)

    def flash(self, strength: float = 0.7) -> None:
        """收到/发出数据时描边亮一下。黑白主题下只有明暗可变化，所以做得很轻。

        起点取当前亮度而不是固定值：数据来得密时是"接着亮"而不是"重新亮一下"，
        连续推 125Hz，看着是一条平滑的明暗起伏。
        """
        self._flash_anim.start(min(1.0, strength), 0.0)

    def start_intro(self, delay_ms: int) -> None:
        fade_in(self, delay_ms)

    # -- 内部 ------------------------------------------------------------

    def _on_flash(self, value) -> None:
        self._flash = float(value)
        self.update()

    def _paint_frost(self, painter: QPainter, path: QPainterPath) -> None:
        """在玻璃面上铺一层散射颗粒。当前主题 / 强度不需要时是空操作。"""
        gain = frost_gain()
        if gain <= 0.0:
            return
        tile = frost_tile(current_scheme(), self.devicePixelRatioF())
        if tile is None:
            return
        painter.save()
        painter.setClipPath(path)
        painter.setOpacity(gain)
        painter.drawTiledPixmap(self.box(), tile)
        painter.restore()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = self.box()
        radius = CARD_RADIUS

        # 1) 阴影。深色主题下 alpha 为 0，直接跳过，省掉一次贴图
        alpha = shadow_alpha()
        if alpha > 0:
            shadow = card_shadow(int(box.width()), int(box.height()), radius, alpha)
            painter.drawPixmap(
                QRectF(box.left() - SHADOW_BLUR, box.top() - SHADOW_BLUR,
                       shadow.width(), shadow.height()),
                shadow, QRectF(shadow.rect()))

        # 2) 卡面：一层半透明面色。页面是纯色，透出来的是那片底色，用来分层次。
        #    面色不能太透，否则上面的文字会跟背景混在一起。
        path = QPainterPath()
        path.addRoundedRect(box, radius, radius)
        painter.fillPath(path, qcolor("surface", glass_alpha()))

        # 3) 磨砂颗粒。背景是纯色、背后没有东西可糊，磨砂感全靠这一层 ——
        #    它是"玻璃自身"的散射，不是背景纹理，所以压在面色**之上**：
        #    画在下面会被半透明的面色盖掉。幅度跟着强度滑杆走（setOpacity 缩放，
        #    不重算贴图），0 是清玻璃，100 是明确的磨砂。
        self._paint_frost(painter, path)

        # 4) 描边。数据流过时按 flash 往 ink 那边压，做出「亮了一下」的感觉
        border = mix(qcolor("line"), qcolor("ink", 110), self._flash)
        pen = QPen(border)
        pen.setWidthF(1.0)
        painter.strokePath(path, pen)

        # 5) 上缘高光：光从上面来，玻璃的顶边会把光兜住 —— 这是"这是一块玻璃板"
        #    最省也最准的一个暗示。做法是沿边缘走一道**自上而下渐隐**的浅色描边，
        #    而不是给整个面板铺渐变（用户否掉的是页面背景的渐变，玻璃自己兜到的光
        #    是另一回事，而且它只落在边缘这一圈上）。
        #    幅度跟磨砂强度走：0 档是清玻璃、没有散射，光边也该一起消失。
        rim_alpha = int(round(RIM_ALPHA * frost_gain()))
        if rim_alpha > 0:
            rim = QLinearGradient(
                box.topLeft(),
                QPointF(box.left(), box.top() + box.height() * RIM_FADE))
            rim.setColorAt(0.0, qcolor("rim", rim_alpha))
            rim.setColorAt(1.0, qcolor("rim", 0))
            painter.strokePath(path, QPen(QBrush(rim), 1.0))

        # 6) 标题行：标题 + 一条细线。小米的卡片头就是靠这条线分层的
        if self._title:
            metrics = QFontMetricsF(self._title_font)
            row_center = box.top() + SHADOW_PAD + TITLE_H * 0.5
            painter.setFont(self._title_font)
            painter.setPen(qcolor("ink"))
            painter.drawText(
                QRectF(box.left() + CARD_PAD, row_center - metrics.height() / 2,
                       box.width() - CARD_PAD * 2, metrics.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._title)
            line_y = row_center + TITLE_H * 0.5
            painter.setPen(QPen(qcolor("line"), 1.0))
            painter.drawLine(QPointF(box.left() + CARD_PAD, line_y),
                             QPointF(box.right() - CARD_PAD, line_y))
