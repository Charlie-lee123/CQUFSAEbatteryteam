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
    QBrush, QColor, QCursor, QFontMetricsF, QImage, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QVBoxLayout, QWidget,
)

from ui_anim import STANDARD, Tween, out_cubic, out_expo
from ui_backdrop import frost_tile, gaussian_blur
from ui_theme import (
    CARD_PAD, CARD_RADIUS, MATERIAL_LIQUID, SHADOW_BLUR, SHADOW_PAD, TITLE_H,
    current_scheme, frost_gain, glass_alpha,
    liquid_alpha, liquid_core_alpha, liquid_inner_alpha, liquid_spec_alpha,
    material, mix, qcolor, shadow_alpha, ui_font,
)

# ---------------------------------------------------------------- 高斯模糊

# 高斯模糊实现见 ui_backdrop.gaussian_blur（卡片阴影与背景虚化共用同一份）

# 玻璃上缘高光。RIM_ALPHA 是最大不透明度（再乘磨砂强度），RIM_FADE 是它往下渐隐到
# 消失的位置（占卡片高度的比例）。太亮会变成"描了道白边"，太暗则完全看不出玻璃板 ——
# 这两个值是在 2 倍屏上目视定的，不是算出来的。
RIM_ALPHA = 84
RIM_FADE = 0.6

# 液态玻璃的内发光内缩量（相对卡片高度的比例上限，逻辑像素封顶）：
# 边缘往里收多少像素再画那圈亮线 —— 收太多就不是"壁内发光"而是画了两个框。
LIQUID_INNER_INSET = 2.0


# ---------------------------------------------------------------- 阴影缓存

_shadows: dict[tuple[int, int, int, int], QPixmap] = {}
_SHADOW_CACHE_LIMIT = 32


# ---------------------------------------------------------------- 材质面
#
# ``paint_glass`` 是**材质感知**的"面"绘制入口：卡片、页头、回放条都走它，
# 按 ``ui_theme.material()`` 分派到毛玻璃或液态玻璃两条渲染路径。
# 两者共享同一根强度（``glass_strength``），各自映射到自己的视觉参数。
#
# ⛔ 一切"面"都必须**自绘**（走 ``paint_glass``），别用 QSS 的 background /
#    border-radius 画面：QSS 对普通 QWidget 的 border-radius 在真实主窗口里会
#    被整条丢掉（2026-10-14 实测：同一份样式表在隔离窗口里是圆的、在主窗口里
#    却是直角，连强制 unpolish/polish 都救不回来），QPlainTextEdit / QLineEdit
#    那类原生控件才稳定 —— "一半圆一半方"就是这么来的。
#
#    （历史上这里还有个不感知材质的 ``paint_surface``，已随 paint_glass 统一而删除。）

def paint_glass(painter: QPainter, box: QRectF, radius: float, *,
                flash: float = 0.0, material_name: str | None = None,
                dpr: float = 1.0,
                fill_override: QColor | None = None,
                border_override: QColor | None = None) -> QPainterPath:
    """按当前材质画一块玻璃面，返回它的圆角路径。

    ``flash``：数据流过时的描边闪烁（0~1，和卡片同一套语义），两种材质都支持。
    ``fill_override``：把主体底色换成别的（回放条那种"次要面"用它）。
    ``border_override``：把描边直接指定（不传就按材质自己算）。
    """
    name = material_name or material()
    path = QPainterPath()
    path.addRoundedRect(box, radius, radius)
    if fill_override is not None:
        painter.fillPath(path, fill_override)
        _paint_glass_overlay(painter, box, radius, path, name, dpr)
    elif name == MATERIAL_LIQUID:
        _paint_liquid(painter, box, radius, path, dpr)
    else:
        _paint_frosted(painter, box, radius, path, dpr)

    # 描边（数据闪一下）。指定了 border_override 就用它，否则按材质算。
    if border_override is not None:
        pen = QPen(border_override)
        pen.setWidthF(1.0)
        painter.strokePath(path, pen)
    else:
        _paint_glass_border(painter, path, name, flash)

    # ⚠️ 上缘高光必须**最后**画：它是一条压在路径上的半透明描边，而上面的"描边"
    #    是不透明的（mix(line, ink, flash)）—— 先画 rim 再画描边的话，描边会把
    #    兜到的那道光整条盖掉（自测"磨砂开着时上缘没兜到光"就是这么挂的：
    #    顶边亮度差只剩 0.58 < 阈值 1.0）。旧 Card.paintEvent 的顺序也是
    #    颗粒 → 面色 → 描边 → rim，这里保持一致。
    if name != MATERIAL_LIQUID:
        _paint_frost_rim(painter, box, path)
    return path


def _paint_frosted(painter: QPainter, box: QRectF, radius: float,
                   path: QPainterPath, dpr: float) -> None:
    """毛玻璃：半透明面色 + 糊过的散射颗粒 + 上缘兜到的光。

    这是项目原有的表现（见模块说明）—— 提高强度 = 越透 + 越毛。颗粒每帧重铺，
    **不能在动画期间跳过**（表面纹理中途消失又回来本身就是一次跳变，2026-10-16
    三次返工换来的结论）。
    """
    alpha = glass_alpha()
    painter.fillPath(path, qcolor("surface", alpha))

    gain = frost_gain()
    if gain > 0.0:
        tile = frost_tile(current_scheme(), dpr)
        if tile is not None:
            painter.save()
            painter.setClipPath(path)
            painter.setOpacity(gain)
            painter.drawTiledPixmap(box, tile)
            painter.restore()
    # 上缘高光不在这里画了 —— 挪到 ``_paint_frost_rim``，由 ``paint_glass``
    # 在描边**之后**统一调用（见那里的说明：先画 rim 再画不透明描边会把光盖掉）。


def _paint_frost_rim(painter: QPainter, box: QRectF, path: QPainterPath) -> None:
    """毛玻璃上缘高光：光从上面来，玻璃的顶边把光兜住。

    幅度跟磨砂强度走 —— 0 档是清玻璃、没有散射，光边也该一起消失。
    ⚠️ 必须在 ``paint_glass`` 的描边**之后**画：它是一条压在路径上的半透明
    描边，而描边是不透明的 —— 先画 rim 再画描边的话，描边会把兜到的那道光
    整条盖掉（自测"磨砂开着时上缘没兜到光"就是这么挂的：顶边亮度差只剩
    0.58 < 阈值 1.0）。
    """
    rim_alpha = int(round(RIM_ALPHA * frost_gain()))
    if rim_alpha > 0:
        rim = QLinearGradient(
            box.topLeft(),
            QPointF(box.left(), box.top() + box.height() * RIM_FADE))
        rim.setColorAt(0.0, qcolor("rim", rim_alpha))
        rim.setColorAt(1.0, qcolor("rim", 0))
        painter.strokePath(path, QPen(QBrush(rim), 1.0))


def _paint_liquid(painter: QPainter, box: QRectF, radius: float,
                  path: QPainterPath, dpr: float) -> None:
    """液态玻璃（Apple Liquid Glass 的桌面近似）—— 苹果 §12「Materials & depth」。

    四件套，从下往上叠：
      ① 半透明主体（强度越高越"实"，才兜得住下面的高光）；
      ② 主体中心提亮（径向渐变）—— 模拟玻璃的厚度 / 折光，"中间那点光"；
      ③ 边缘内发光 —— 让主体边缘亮一圈，像光被收在玻璃壁里；
      ④ 镜面双色调描边 —— 左上一条亮边、右下一条暗边（见 _paint_glass_border）。
    ①②③ 在本函数里画，④ 由共享的描边步骤画（这样 flash 也能落在它上面）。
    """
    alpha = liquid_alpha()
    painter.fillPath(path, qcolor("surface", alpha))

    # ② 中心提亮：以卡面中心为圆心的径向渐变。裁到路径里，只作用在面上。
    core = liquid_core_alpha()
    if core > 0:
        painter.save()
        painter.setClipPath(path)
        center = box.center()
        gradient = QRadialGradient(
            center, max(box.width(), box.height()) * 0.62)
        gradient.setColorAt(0.0, qcolor("glass_hi", core))
        gradient.setColorAt(0.65, qcolor("glass_hi", int(core * 0.35)))
        gradient.setColorAt(1.0, qcolor("glass_hi", 0))
        painter.fillPath(path, QBrush(gradient))
        painter.restore()

    # ③ 边缘内发光：往内收 2px 再描一圈很淡的亮线。
    inner = liquid_inner_alpha()
    if inner > 0:
        inset = QRectF(box).adjusted(LIQUID_INNER_INSET, LIQUID_INNER_INSET,
                                     -LIQUID_INNER_INSET, -LIQUID_INNER_INSET)
        inner_path = QPainterPath()
        inner_path.addRoundedRect(inset, max(1.0, radius - LIQUID_INNER_INSET),
                                  max(1.0, radius - LIQUID_INNER_INSET))
        top_glow = QLinearGradient(
            box.topLeft(), QPointF(box.right(), box.bottom()))
        top_glow.setColorAt(0.0, qcolor("glass_hi", inner))
        top_glow.setColorAt(1.0, qcolor("glass_hi", 0))
        painter.strokePath(inner_path, QPen(QBrush(top_glow), 1.0))


def _paint_glass_overlay(painter: QPainter, box: QRectF, radius: float,
                         path: QPainterPath, name: str, dpr: float) -> None:
    """``fill_override`` 时的轻量叠加（回放条这种次要面）：只补材质特征，不铺主体色。"""
    if name != MATERIAL_LIQUID:
        return
    core = liquid_core_alpha()
    if core > 0:
        painter.save()
        painter.setClipPath(path)
        gradient = QRadialGradient(
            box.center(), max(box.width(), box.height()) * 0.62)
        gradient.setColorAt(0.0, qcolor("glass_hi", int(core * 0.6)))
        gradient.setColorAt(1.0, qcolor("glass_hi", 0))
        painter.fillPath(path, QBrush(gradient))
        painter.restore()


def _paint_glass_border(painter: QPainter, path: QPainterPath, name: str,
                        flash: float) -> None:
    """描边。

    · 毛玻璃：常态是 line，数据流过时按 flash 往 ink 压（做出"亮了一下"）。
    · 液态玻璃：**镜面双色调**描边 —— 沿路径走一道从左上（亮）到右下（暗）的
      渐变，就是苹果那句 "bright top edge = light catching the material" 的桌面版；
      再叠一层很轻的 ink 描边表达 flash。
    """
    if name == MATERIAL_LIQUID:
        spec = liquid_spec_alpha()
        if spec > 0:
            gradient = QLinearGradient(path.boundingRect().topLeft(),
                                       path.boundingRect().bottomRight())
            gradient.setColorAt(0.0, qcolor("glass_hi", spec))
            gradient.setColorAt(0.5, qcolor("glass_hi", int(spec * 0.20)))
            gradient.setColorAt(1.0, qcolor("glass_lo", int(spec * 0.85)))
            painter.strokePath(path, QPen(QBrush(gradient), 1.0))
        if flash > 0.0:
            painter.strokePath(path, QPen(qcolor("ink", int(120 * flash)), 1.0))
        return
    border = mix(qcolor("line"), qcolor("ink", 110), flash)
    pen = QPen(border)
    pen.setWidthF(1.0)
    painter.strokePath(path, pen)


# 阴影尺寸的量化步长。⛔ 缓存键必须量化：动画期间卡片**每帧尺寸都不同**，
# 按精确尺寸做键 = 每帧都 miss = 每帧重算一次高斯模糊。实测单次 22ms（1500x900
# 的卡片），两三张卡同时在动就是 40~60ms/帧 —— 这就是"动画一卡一卡、末尾追平"
# 的真正来源（逐帧日志表现为"全部控件同一时刻停滞 15~20ms"）。
# 量化到 32px 后，一次 200px 的动画只重建 6 次阴影，其余全部命中缓存。
_SHADOW_STEP = 48
# 阴影构建的降采样倍数（见 card_shadow）：2 = 半分辨率，成本约 1/4。
_SHADOW_SUPERSAMPLE = 2


def _quantize_shadow(size: int) -> int:
    """向上取整到 _SHADOW_STEP 的倍数（宁可大一点：阴影大一圈看不出来，小一圈会缺角）。"""
    return max(_SHADOW_STEP, int((size + _SHADOW_STEP - 1) // _SHADOW_STEP) * _SHADOW_STEP)


def _anim_running() -> bool:
    """当前是否有补间在跑（有的话阴影允许"拉伸凑合"，见 card_shadow）。"""
    try:
        from ui_anim import ticker_active
        return ticker_active()
    except Exception:                  # noqa: BLE001 - 取不到就按"没在动"
        return False


def card_shadow(width: int, height: int, radius: int, alpha: int) -> QPixmap:
    """卡片阴影：圆角剪影模糊一次后缓存。**尺寸量化**后取（见 _SHADOW_STEP）。

    返回的位图尺寸可能比请求的大（最多 32px）—— 调用方按目标矩形**缩放**贴，
    避免动画期间每帧重建模糊。
    """
    width, height = _quantize_shadow(width), _quantize_shadow(height)
    key = (width, height, radius, alpha)
    cached = _shadows.get(key)
    if cached is not None:
        return cached
    # ⛔ 动画期间**直接复用已有阴影**（任意尺寸，调用方会缩放到精确大小）：
    #    尺寸每帧都在变，再量化也还是要每跨一档重建一次（单次 ~12ms，一次
    #    200ms 动画要撞 5 次 = 5 次可感知的顿）。卡片在动的时候没人看得出一
    #    张模糊阴影被拉伸了几十像素；动画一停，下一帧就会按正确尺寸重建。
    if _anim_running():
        for (rw, rh, rr, ra), pix in _shadows.items():
            if rr == radius and ra == alpha:
                return pix
    if len(_shadows) >= _SHADOW_CACHE_LIMIT:
        _shadows.clear()

    # ⛔ **半分辨率构造**：阴影是一条低频模糊渐变，用一半分辨率画剪影、
    #    按一半的半径模糊、再放大回来，肉眼看不出差别，成本却只有 1/4。
    #    实测 1500x900 的卡片：全分辨率 22ms → 半分辨率约 5ms（动画期间
    #    每跨一个量化档才重建一次，摊到每帧基本不可见）。
    s = _SHADOW_SUPERSAMPLE
    spread = SHADOW_BLUR
    half_w, half_h = max(1, width // s), max(1, height // s)
    pad = max(2, spread // s)
    image = QImage(half_w + pad * 2, half_h + pad * 2,
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    silhouette = QPainterPath()
    silhouette.addRoundedRect(QRectF(pad, pad, half_w, half_h),
                              max(1.0, radius / s), max(1.0, radius / s))
    painter.fillPath(silhouette, QColor(0, 0, 0, alpha))
    painter.end()

    blurred = gaussian_blur(image, max(1, (SHADOW_BLUR // 2) // s))
    full = blurred.scaled(width + spread * 2, height + spread * 2,
                          Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    shadow = QPixmap.fromImage(full)
    _shadows[key] = shadow
    return shadow



# ---------------------------------------------------------------- 入场淡入

def fade_in(widget: QWidget, delay_ms: int = 0, duration_ms: int = 240) -> None:
    """入场淡入。短、快、收得住，不做过冲也不做扫光。

    曲线用设计系统的 STANDARD（起步略缓、收尾极稳）：``out_cubic`` 起步就是最大
    速度，用在"内容出现"上像被甩进来；出现类动效该是"被放上去"的。

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

    def apply(value: float) -> None:
        # ⚠️ 效果会**随控件一起析构**，而补间是延后 delay_ms 才起跑的：
        # 控件（或整个窗口）在这之前被销毁的话，这一帧就会踩到已删除的
        # QGraphicsOpacityEffect（实测收尾必现 RuntimeError）。踩到就停表 ——
        # 效果都没了，这波动画本来也没什么可做的了。
        try:
            effect.setOpacity(value)
        except RuntimeError:
            tween.stop()

    # 补间挂在控件上，由控件持有它的生命周期
    tween = Tween(duration_ms, STANDARD, apply, finish, parent=widget)
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
        # 悬停微抬升（0~1）。苹果 §1「即时反馈」的延伸：鼠标进到卡面上，卡片自己
        # 抬一下，说明"这块能交互"。只挪**绘制的 box**，不动几何（布局一个字节不碰）。
        self._lift = 0.0
        self._header_buttons: list[QWidget] = []
        self._title_font = ui_font(13.5, ui_font().weight())
        # 描边闪一下用的补间：复用同一个，数据一密也不会叠出多个动画互相打架
        # 描边闪烁走指数衰减（out_expo）：瞬间亮起、余晖拖长，比 cubic 更像
        # "被注意到一眼就散掉"，而不是被拉长成一段亮边。
        self._flash_anim = Tween(380, out_expo, self._on_flash, parent=self)
        # 悬停抬升的补间：进入略快、退出略缓（和按钮同一套手感）。
        self._lift_anim = Tween(180, out_cubic, self._on_lift, parent=self)
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

    def enterEvent(self, event) -> None:
        self._animate_lift(1.0, 170)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        # ⚠️ 光标从卡片移到它的**子控件**上时，Qt 也会给卡片发 Leave —— 卡面被
        #    子控件铺满，不判断的话悬停会不停"抬起-落下"地抖。用鼠标实际位置
        #    判断是否还在卡里：还在就忽略这次 Leave。
        if self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            return
        self._animate_lift(0.0, 200)
        super().leaveEvent(event)

    def _animate_lift(self, target: float, duration: int) -> None:
        self._lift_anim.start(self._lift, target, duration)

    def _on_lift(self, value) -> None:
        self._lift = float(value)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = self.box()
        # 悬停时整块**绘制面**上移最多 2px（不动几何）—— 苹果 §1 的"即时反馈"。
        lift = self._lift * 2.0
        if lift > 0.05:
            box = box.translated(0.0, -lift)
        radius = CARD_RADIUS

        # 1) 阴影。深色主题下 alpha 为 0，直接跳过，省掉一次贴图。
        #    抬升时把阴影也带上去一点、略微加大，抬升才"有影可依"。
        alpha = shadow_alpha()
        if alpha > 0:
            shadow = card_shadow(int(box.width()), int(box.height()), radius, alpha)
            # ⚠️ 目标矩形按**请求尺寸**算、位图按它的实际尺寸缩放贴：阴影位图
            #    现在可能比卡片大最多 32px（量化缓存），直接按位图尺寸贴会让
            #    剪影整体偏移、右下角多出一圈。
            painter.drawPixmap(
                QRectF(box.left() - SHADOW_BLUR, box.top() - SHADOW_BLUR,
                       box.width() + SHADOW_BLUR * 2,
                       box.height() + SHADOW_BLUR * 2),
                shadow, QRectF(shadow.rect()))

        # 2) 材质面：按当前材质（毛玻璃 / 液态玻璃）画主体、颗粒 / 镜面高光、描边。
        #    flash 仍由材质描边表达（"亮了一下"）。
        paint_glass(painter, box, radius, flash=self._flash,
                    dpr=self.devicePixelRatioF())

        # 3) 标题行：标题 + 一条细线。小米的卡片头就是靠这条线分层的
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
