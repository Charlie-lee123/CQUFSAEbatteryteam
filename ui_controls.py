"""自绘控件：主按钮、开关、活动指示。

黑白系统里没有色相可以承载强调，只能靠反白：浅色主题下主按钮是墨色实底、
反白文字，深色主题下反过来。这是黑白界面里最经得起看的一种做法。

控件都从原生控件派生，``setChecked()`` / ``isChecked()`` / ``toggled`` /
``clicked`` 这些 API 原样保留，调用方（包括 selftest）不用改，只换长相。

动效克制：小米那套是「短、快、收得住」——160ms 上下的颜色过渡，
不做外发光、不做扩散环、不做弹跳。过渡一律走 ``ui_anim`` 那套 120fps 的
非线性补间，不用 Qt 自带的动画（它的节拍约 60fps，高刷屏上能看出台阶）。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEvent, QLineF, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QFontMetrics, QFontMetricsF, QImage, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider,
    QSpinBox, QStyle, QStyledItemDelegate, QWidget,
)

from ui_anim import (STANDARD, Tween, in_out_quint, linear, out_cubic, out_expo,
                     out_quint)
from ui_theme import (
    CAPTION_BTN_H, CAPTION_BTN_W, GLYPH_CLOSE, GLYPH_COLLAPSE, GLYPH_EXPAND_CHEVRON,
    SWITCH_KNOB, SWITCH_ON, icon_font, mix, qcolor, switch_off_color, ui_font,
)


# ------------------------------------------------------------ 文字墨迹测量

# 「文字在框里到底偏上还是偏下」不做任何字体度量推算，**直接看渲染结果**。
# 教训（2026-10-12）：QFontMetrics.tightBoundingRect 不认字体回退 ——
# "5000 行" 的 "行" 由回退字体画出来，tight 报的下界比真实墨迹低 1.4px；
# 而 QLineEdit 内部的行盒又按回退后的字号重新居中（基线整体上移 2px）。
# 两处都不可推算。抓一次渲染按明暗差扫，两个问题一起消失。

_EDIT_INK_CACHE: dict[tuple, float | None] = {}
_TEXT_INK_CACHE: dict[tuple, tuple | None] = {}


def _edit_ink_offset(edit: QLineEdit) -> float | None:
    """行内编辑框里**文字墨迹**的竖直中心（相对编辑框顶部，逻辑像素）。

    抓编辑框自己的渲染扫明暗差：背景是否透明、有没有边框、行盒多高、
    用了哪个回退字体，全都自动包含在画面里。测不到就返回 None，调用方
    回退到旧的字号线性近似。

    ⚠️ 聚焦时光标、全选时的高亮块都会撑满整行、把测量整个污染掉 ——
    这两种情况一律返回 None（宁可不摆，也好过照着错的目标摆）。
    """
    if not isinstance(edit, QLineEdit):
        return None
    text = edit.text()
    height = edit.height()
    if not text or height <= 0 or edit.hasFocus() or edit.hasSelectedText():
        return None
    dpr = edit.devicePixelRatioF() or 1.0
    key = (edit.font().key(), text, height, round(dpr, 2))
    if key in _EDIT_INK_CACHE:
        return _EDIT_INK_CACHE[key]
    gray = edit.grab().toImage().convertToFormat(
        QImage.Format.Format_Grayscale8)
    width, ih = gray.width(), gray.height()
    data = gray.constBits().tobytes()
    bpl = gray.bytesPerLine()
    rows = []
    for y in range(ih):
        row = data[y * bpl:y * bpl + width]
        if row and (max(row) - min(row)) > 60:
            rows.append(y)
    offset = None
    if rows:
        offset = ((rows[0] + rows[-1] + 1) / 2.0) / dpr
    _EDIT_INK_CACHE[key] = offset
    return offset


def _combo_label_ink_offset(combo) -> float | None:
    """不可编辑下拉框里**文字墨迹**的竖直中心（相对控件顶部，逻辑像素）。

    和 _edit_ink_offset 同一思路（抓渲染扫明暗差），但文字由样式直接画、没有
    可移动的内部编辑框 —— 扫的时候要避开右侧自绘的 chevron。
    """
    text = combo.currentText()
    height = combo.height()
    if not text or height <= 0:
        return None
    # ⚠️ 控件被压得很窄时（比如右侧栏正在做"向右折叠"动画）抓出来的是无效图像，
    #    `constBits()` 返回 None，下面一行就崩（实测：折叠时 QComboBox 0 宽）。
    #    这种尺寸下本来也量不出墨迹，直接给 None（调用方会退回默认偏移）。
    if combo.width() < 24 or combo.height() < 8:
        return None
    dpr = combo.devicePixelRatioF() or 1.0
    gray = combo.grab().toImage().convertToFormat(
        QImage.Format.Format_Grayscale8)
    width, ih = gray.width(), gray.height()
    if gray.isNull() or width < 8 or ih < 4:
        return None
    bits = gray.constBits()
    if bits is None:
        return None
    data = bits.tobytes()
    bpl = gray.bytesPerLine()
    x_lo = int(4 * dpr)
    x_hi = max(width - int(30 * dpr), x_lo + 1)     # 右边 30px 是 chevron 区
    rows = []
    for y in range(ih):
        row = data[y * bpl:y * bpl + width]
        segment = row[x_lo:x_hi]
        if segment and (max(segment) - min(segment)) > 60:
            rows.append(y)
    if not rows:
        return None
    return ((rows[0] + rows[-1] + 1) / 2.0) / dpr


def text_ink(font, text: str, dpr: float = 2.0) -> tuple | None:
    """把文字渲染成小图量出墨迹盒 ``(左, 上, 右, 下)``——相对文本原点与基线。

    给**自己画的**文字用（InkButton 的 drawText）：那里没有现成的渲染画面可抓，
    就现画一张。含字体回退的真实墨迹，比任何 QFontMetrics 推算都准。
    """
    if not text:
        return None
    key = (font.key(), text, round(dpr, 2))
    if key in _TEXT_INK_CACHE:
        return _TEXT_INK_CACHE[key]
    fm = QFontMetricsF(font)
    pad = 6
    width = int(fm.horizontalAdvance(text)) + pad * 2
    height = int(fm.ascent() + fm.descent()) + pad * 2
    # ⚠️ QImage 按**物理像素**建（×dpr）；setDevicePixelRatio 只是给坐标映射用，
    # 不会把画布放大 —— 搞反的话 dpr=2 时画布只有一半大，文字直接被裁掉。
    image = QImage(max(int(width * dpr), 4), max(int(height * dpr), 4),
                   QImage.Format.Format_Grayscale8)
    image.setDevicePixelRatio(dpr)
    image.fill(0)
    painter = QPainter(image)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(QPointF(pad, fm.ascent() + pad), text)
    painter.end()
    data = image.constBits().tobytes()
    bpl = image.bytesPerLine()
    width, height = image.width(), image.height()
    left = right = top = bottom = None
    for y in range(height):
        row = data[y * bpl:y * bpl + width]
        xs = [x for x, v in enumerate(row) if v > 60]
        if xs:
            if top is None:
                top = y
            bottom = y
            left = xs[0] if left is None else min(left, xs[0])
            right = xs[-1] if right is None else max(right, xs[-1])
    if left is None:
        result = None
    else:
        result = (left / dpr - pad, top / dpr - fm.ascent() - pad,
                  (right + 1) / dpr - pad, (bottom + 1) / dpr - fm.ascent() - pad)
    _TEXT_INK_CACHE[key] = result
    return result


def optical_text_shift(font, text: str, dpr: float = 2.0) -> float:
    """文字块要**往下**挪多少才视觉居中（正 = 下移）。

    Qt 画单行文字时按"行盒"垂直居中：基线落在 ``中心 + (ascent-descent)/2``，
    而墨迹中心相对基线是 ``(top+bottom)/2`` —— 两者相加就是墨迹中心偏离
    控件中心的量，取负即为修正量。
    """
    ink = text_ink(font, text, dpr)
    if ink is None:
        return 0.0
    fm = QFontMetricsF(font)
    return -((fm.ascent() - fm.descent()) / 2.0 + (ink[1] + ink[3]) / 2.0)


# ------------------------------------------------------------------ 主按钮

class InkButton(QPushButton):
    """药丸按钮。primary 是墨色实底（主操作），ghost 是浅底描边（次操作）。"""

    def __init__(self, text: str = "", variant: str = "ghost",
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._variant = variant
        self._hover = 0.0
        self._press = 0.0
        # 可用度 0~1（禁用态过渡用）。初值按当前状态，避免首帧闪一下。
        self._enabled_t = 1.0 if self.isEnabled() else 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(ui_font(12.5, ui_font().weight()))
        # 让全局 QPushButton 规则整块放过：不排除的话会在自绘圆角外再描一圈轮廓
        self.setObjectName("inkButton")
        # 尺寸必须自己给死。QSS 里 #inkButton 把 padding 清零了（自绘不需要它），
        # 结果按钮只剩文字宽度 —— 两个字的按钮就 34px 宽，文字顶到药丸边缘，
        # 看着像没画背景。这里按「文字 + 左右内边距」算出最小尺寸并锁住。
        self._apply_size_hint()
        # 悬停是一进一出两段，时长不同（进入略快、退出略缓），所以时长在
        # enter/leave 里各自给；补间本身复用同一个，随时打断也不会跳。
        self._hover_anim = Tween(160, out_cubic, self._on_hover, parent=self)
        # 按下反馈走一段 110ms 的短补间：苹果 §1 要"按下即时反馈"，但颜色的
        # 0→1 **硬切**在高刷屏上是一次跳变 —— 110ms 感知上仍是"即刻"，又平滑。
        self._press_anim = Tween(120, out_cubic, self._on_press, parent=self)
        # 禁用/启用之间的过渡（灰化不是"啪"一下，而是很快地淡过去）。
        self._enabled_anim = Tween(160, out_cubic, self._on_enabled, parent=self)

    def _apply_size_hint(self) -> None:
        metrics = QFontMetrics(self.font())
        width = metrics.horizontalAdvance(self.text()) + 44
        height = max(34, metrics.height() + 16)
        self.setMinimumSize(width, height)

    def setText(self, text: str) -> None:      # noqa: N802 (Qt 命名)
        super().setText(text)
        if hasattr(self, "_hover_anim"):
            self._apply_size_hint()

    def enterEvent(self, event) -> None:
        self._animate_to(1.0, 150)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._animate_to(0.0, 220)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        self._press_anim.start(self._press, 1.0, 110)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._press_anim.start(self._press, 0.0, 160)
        super().mouseReleaseEvent(event)

    def _animate_to(self, target: float, duration: int) -> None:
        self._hover_anim.start(self._hover, target, duration)

    def _on_hover(self, value) -> None:
        self._hover = float(value)
        self.update()

    def _on_press(self, value) -> None:
        self._press = float(value)
        self.update()

    def _on_enabled(self, value) -> None:
        self._enabled_t = float(value)
        self.update()

    def changeEvent(self, event) -> None:
        # 启用/禁用之间补一段过渡（调用方 setEnabled 很频繁，硬切会显脏）。
        if event.type() == QEvent.Type.EnabledChange:
            self._enabled_anim.start(
                self._enabled_t, 1.0 if self.isEnabled() else 0.0, 160)
        super().changeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -1.0, -1.0)
        radius = rect.height() / 2.0          # 药丸
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        if self._variant == "primary":
            base = qcolor("fill_primary")
            hot = qcolor("fill_primary_hover")
            text_color = qcolor("on_primary")
        else:
            base = qcolor("surface_2")
            hot = qcolor("surface_hover")
            text_color = qcolor("ink")

        lit = max(self._hover, self._press)
        # 先把**可用态**画法定出来，再按 (1 - _enabled_t) 朝禁用态混合 ——
        # 这样启用/禁用之间是"淡过去"，不是硬切。
        fill = mix(base, hot, lit)
        border = (mix(qcolor("line"), qcolor("line_strong"), lit)
                  if self._variant == "ghost" else None)
        dim = 1.0 - self._enabled_t
        if dim > 0.0:
            transparent = QColor(0, 0, 0, 0)
            fill = mix(fill, transparent, dim)
            text_color = mix(text_color, qcolor("ink_disabled"), dim)
            border = mix(border if border is not None else transparent,
                         qcolor("line"), dim)
        painter.fillPath(path, fill)
        if border is not None and border.alpha() > 0:
            pen = QPen(border)
            pen.setWidthF(1.0)
            painter.strokePath(path, pen)

        painter.setPen(text_color)
        painter.setFont(self.font())
        # 文字在药丸里做**视觉**居中：Qt 的 AlignCenter 按"行盒"居中，而汉字/
        # 数字的字形在行盒里天然偏下（用户点名"与上下边框距离不等"）。按实测
        # 墨迹算一个修正量（optical_text_shift），只挪文字、不挪药丸。
        dy = optical_text_shift(self.font(), self.text(),
                                self.devicePixelRatioF() or 2.0)
        painter.drawText(rect.adjusted(0.0, dy, 0.0, dy),
                         Qt.AlignmentFlag.AlignCenter, self.text())


# ------------------------------------------------------------------ 图标按钮

class IconButton(QPushButton):
    """Windows 风格的图标按钮。

    反馈方式照 Windows 11 的命令按钮来：平时无边框无底色，悬停时浮出一层圆角浅底，
    按下再深一档 —— 不靠描边来划分区域，视觉上安静得多。

    variant="flat" 是常规命令按钮（界面里现在统一用这一档，无静息底色）；
    "filled" 是实心主操作按钮，保留给将来真正需要色块强调的场景。
    字形来自系统图标字体，所以每个按钮都必须有 tooltip，否则图标靠猜。

    ``hover_fill=False`` 连悬停浮出的那层浅底也一并去掉，按钮**任何时候都是透明的**，
    只靠字形本身由 ink_2 提亮到 ink 来给反馈。底栏那颗齿轮用的就是这一档 ——
    它孤零零地贴在左下角，浮出一块圆角方块在那片干净的底栏上很显眼（深色下尤其），
    而它旁边就是一行没人会误点的状态文字，"按下去是它"这件事字形变亮已经说得够清楚。
    """

    def __init__(self, glyph: str, tooltip: str = "", size: int = 30,
                 glyph_size: int = 15, variant: str = "flat",
                 hover_fill: bool = True, cursor: str = "pointing",
                 parent: QWidget | None = None, width: int | None = None) -> None:
        super().__init__("", parent)
        self._glyph = glyph
        self._variant = variant
        self._hover_fill = hover_fill
        self._hover = 0.0
        self._press = 0.0
        self._active = False
        # 可用度 0~1（禁用态过渡用，见 changeEvent / paintEvent）
        self._enabled_t = 1.0 if self.isEnabled() else 0.0
        # width 给了就是**矩形**按钮（标题栏那三颗），不给就是正方形
        self.setFixedSize(width or size, size)
        # 光标形状可挑：绝大多数按钮用"手"暗示可点；复制这类"指着眼前的数据"
        # 的按钮用箭头更合适（用户点名：复制按钮悬停保持箭头）。
        self.setCursor(Qt.CursorShape.ArrowCursor if cursor == "arrow"
                       else Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        # 自绘，所以要绕开全局 QPushButton 规则，否则会在自绘圆角外再描一圈
        self.setObjectName("iconButton")
        self._glyph_font = icon_font(glyph_size)
        self._hover_anim = Tween(150, out_cubic, self._on_hover, parent=self)
        # 按下即时反馈走短补间（同 InkButton）：0→1 硬切在高刷屏上是一次跳变。
        self._press_anim = Tween(120, out_cubic, self._on_press, parent=self)
        # 启用/禁用过渡
        self._enabled_anim = Tween(160, out_cubic, self._on_enabled, parent=self)

    def _radius(self) -> float:
        """悬停色块的圆角半径。正方形按钮按宽度取一个比例上的"方中带圆"，
        矩形按钮（标题栏那三颗）要在子类里改小 —— 见 ``CaptionButton``。"""
        return self.width() * 0.26

    def set_glyph(self, glyph: str) -> None:
        self._glyph = glyph
        self.update()

    def set_active(self, active: bool) -> None:
        """激活态：字形和圆角描边染上主题蓝，表示"这个功能正开着"。

        给暂停键这类**两态**按钮用 —— 光把两竖换成三角太小，用户根本注意不到
        状态变过（用户反馈过"按了没反应"）。界面唯一的功能色就是开关那支蓝，
        激活 = 有色的语义和它保持一致。
        """
        if self._active == active:
            return
        self._active = active
        self.update()

    def enterEvent(self, event) -> None:
        self._animate_to(1.0, 140)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._animate_to(0.0, 200)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        self._press_anim.start(self._press, 1.0, 110)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._press_anim.start(self._press, 0.0, 160)
        super().mouseReleaseEvent(event)

    def _animate_to(self, target: float, duration: int) -> None:
        self._hover_anim.start(self._hover, target, duration)

    def _on_hover(self, value) -> None:
        self._hover = float(value)
        self.update()

    def _on_press(self, value) -> None:
        self._press = float(value)
        self.update()

    def _on_enabled(self, value) -> None:
        self._enabled_t = float(value)
        self.update()

    def changeEvent(self, event) -> None:
        # 一部分图标按钮会被 show/hide 或可用性切换（例如"保存"在一无数据时禁用），
        # 硬切灰色同样是一次跳变 —— 补一段很快的过渡。
        if event.type() == QEvent.Type.EnabledChange:
            self._enabled_anim.start(
                self._enabled_t, 1.0 if self.isEnabled() else 0.0, 160)
        super().changeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -1.0, -1.0)
        radius = self._radius()
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        lit = max(self._hover, self._press)
        # 先算**可用态**的外观，再按 (1 - _enabled_t) 朝禁用态混 —— 见 InkButton。
        fill = QColor(0, 0, 0, 0)
        border = None
        if self._active:
            # 激活态：淡蓝圆角底 + 细蓝描边 + 蓝字形 —— 压过悬停/按下的灰阶反馈，
            # 状态一眼可见（两竖换三角太小，用户反馈过"按了没反应"）
            fill = QColor(SWITCH_ON)
            fill.setAlpha(38)                 # 淡淡一层底，不做成实心色块
            border = QColor(SWITCH_ON)
            glyph_color = QColor(SWITCH_ON)
        elif self._variant == "filled":
            base = qcolor("fill_primary")
            fill = mix(base, qcolor("fill_primary_hover"), lit)
            glyph_color = qcolor("on_primary")
        else:
            # 平时完全透明，只有悬停/按下才浮出底色；
            # hover_fill=False 的（底栏齿轮）连这一层也不画，永远只留字形
            if self._hover_fill:
                fill = mix(qcolor("surface_hover", 0), qcolor("surface_hover"),
                           max(lit, 0.0) if lit > 0.01 else 0.0)
            # ⚠️ 静止态直接用 ink（243，与卡片标题同亮）。图标是**细笔画**，经
            #    抗锯齿后峰值比同色文字低一大截，之前"往 ink 混 35%/55%"每档只差
            #    10-15%，肉眼看不出来（用户连说两轮"改了个寂寞"）。悬停反馈本来就
            #    靠背景药丸（hover_fill），不靠提亮。
            glyph_color = qcolor("ink")

        dim = 1.0 - self._enabled_t
        if dim > 0.0:
            transparent = QColor(0, 0, 0, 0)
            fill = mix(fill, transparent, dim)
            glyph_color = mix(glyph_color, qcolor("ink_disabled"), dim)
            if border is not None:
                border = mix(border, transparent, dim)

        if fill.alpha() > 0:
            painter.fillPath(path, fill)
        if border is not None and border.alpha() > 0:
            painter.setPen(QPen(border, 1.0))
            painter.drawPath(path)

        self._draw_glyph(painter, rect, glyph_color)

    def _draw_glyph(self, painter, rect, color) -> None:
        """画图标本体。默认就是"把字形居中"；子类可以换一种画法。

        抽成方法是为了 ``AlertButton`` —— 它要的是两色自绘图形，
        而字形这条路一个图标只有一个颜色。
        """
        painter.setPen(color)
        painter.setFont(self._glyph_font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._glyph)


class CaptionButton(IconButton):
    """标题栏三颗键（最小化 / 最大化还原 / 关闭）。

    和普通图标按钮的差别只有**形状**：矩形 46×30、圆角很小（6px，跟着系统那套
    "方角 + 轻微圆角"的悬停色块），而不是正方形的大圆角块。其余全部继承 ——
    悬停/按下的补间、透明静息态、`set_active` 都在 ``IconButton`` 里，这样三颗键
    的手感和界面其它图标按钮是同一条曲线。

    ⚠️ 静止时**不铺底色**（和 IconButton 一样），只有悬停/按下才浮出色块 ——
    这正是 Windows 标题栏的行为；关掉键不做成红色也是刻意的：界面规矩是
    "黑白灰 + 开关那一支蓝"，插一个系统红会破坏唯一的功能色语义。
    """

    def __init__(self, glyph: str, tooltip: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(glyph, tooltip, size=CAPTION_BTN_H, glyph_size=11,
                         hover_fill=True, parent=parent, width=CAPTION_BTN_W)

    def _radius(self) -> float:
        return 6.0


class AlertButton(IconButton):
    """「定位最近一次异常」按钮：**红色感叹号**。

    为什么自绘而不是用 Segoe 的字形（E7BA / E814…）：那些字形整枚图标跟着按钮的
    灰阶反馈走，给不了"固定是警示红"这个要求。

    造型：一个粗感叹号（圆头竖线 + 圆点）。用户先要过"圆角三角形内含红色感叹号"，
    后来改成"三角形不要了，只保留红色感叹号" —— 所以这里**没有**三角形，
    悬停时红色往白里提一点当反馈（底下还有一层圆角色块兜着）。
    """

    def __init__(self, tooltip: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__("", tooltip, size=30, glyph_size=15, parent=parent)

    def _draw_glyph(self, painter, rect, color) -> None:
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        lit = max(self._hover, self._press)
        # 悬停/按下时往白里提一点：纯固定红在悬停时毫无反应，手感会跟其它图标按钮脱节
        red = mix(QColor(qcolor("alert_red")), QColor("#FFFFFF"), 0.22 * lit)

        pen = QPen(red, 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(cx, cy - 6.4), QPointF(cx, cy + 1.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(red)
        painter.drawEllipse(QPointF(cx, cy + 4.4), 1.5, 1.5)



# ------------------------------------------------------------------ 开关

class Switch(QCheckBox):
    """胶囊开关。开启时**蓝色轨道 + 白色圆点**，浅色和深色主题完全一致。

    开关是状态指示，用固定色比"跟着主题反白"更容易一眼读懂。
    关闭态的轨道仍跟主题走（浅灰 / 深灰），否则分不出开还是关。
    """

    TRACK_W = 40.0
    TRACK_H = 23.0
    GAP_PX = 10.0

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._t = 1.0 if self.isChecked() else 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(ui_font(11.0))
        # 无障碍：自绘控件没有原生勾选框，读屏软件拿不到任何线索 —— 必须
        # 显式给出角色、名称与状态。名称随文字走；「定制功能」区里开关自己
        # 不带文字（名字在左侧 QLabel 上），由 set_accessible_label 补。
        self.setAccessibleName(text)
        self.toggled.connect(self._on_accessible_state)
        self._sync_accessible_state()
        self.toggled.connect(self._on_toggled)
        # 滑块位移是小位移，用 out_quint 起步更干脆、收尾更慢，看着"贴手"
        self._knob_anim = Tween(190, out_quint, self._on_value, parent=self)

    def set_accessible_label(self, name: str, description: str = "") -> None:
        """给"无文字开关"补上读屏名称/说明（它自己画不出文字时读屏是哑的）。"""
        self.setAccessibleName(name)
        if description:
            self.setAccessibleDescription(description)

    def _sync_accessible_state(self) -> None:
        self.setAccessibleDescription("已开启" if self.isChecked() else "已关闭")

    def _on_accessible_state(self, _checked: bool) -> None:
        self._sync_accessible_state()

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self.font())
        width = int(self.TRACK_W)
        if self.text():
            width += int(self.GAP_PX) + metrics.horizontalAdvance(self.text())
        return QSize(width, max(int(self.TRACK_H) + 6, metrics.height() + 2))

    minimumSizeHint = sizeHint

    def _track_rect(self) -> QRectF:
        """胶囊轨道在控件里的位置（与 paintEvent 的绘制几何保持一致）。"""
        return QRectF(0.0, (self.height() - self.TRACK_H) / 2.0,
                      self.TRACK_W, self.TRACK_H)

    def hitButton(self, pos: QPoint) -> bool:
        """命中区按**圆角胶囊轨道**（及其右侧的开关文字）算，不是整个控件矩形。

        ⚠️ 两个历史坑叠在一起，这里必须同时避开：
        1) QCheckBox 默认命中区按**原生勾选框**（Fusion 的 14px 小方框）算，
           跟自绘的 40px 胶囊轨道对不上 —— 点轨道中段、右半边全是"没反应"
           （用户报"部分拨动开关无法点击或无法切换状态"）；
        2) 一度改成 ``rect().contains(pos)``（整个控件矩形）修上一条 —— 于是
           横向占满整行、纵向占满控件高度（含轨道上下的留白）的区域都能触发，
           "停在开关同一高度随便点一下也切换"（用户报的新问题）。
        正确做法就是**几何跟着绘制走**：轨道内用胶囊判定（四角按圆角切掉），
        轨道右侧的文字区矩形照常可点，其余留白一律不响应。
        """
        point = QPointF(pos)
        track = self._track_rect()
        if track.contains(point):
            # 胶囊：把四个角的圆外区域排除 —— 与 fillPath 画的形状一致
            radius = self.TRACK_H / 2.0
            cx = min(max(point.x(), track.left() + radius), track.right() - radius)
            cy = min(max(point.y(), track.top() + radius), track.bottom() - radius)
            return QLineF(point, QPointF(cx, cy)).length() <= radius
        if self.text():
            text_rect = QRectF(self.TRACK_W + self.GAP_PX, 0.0,
                               self.width() - self.TRACK_W - self.GAP_PX,
                               float(self.height()))
            return text_rect.contains(point)
        return False

    def mousePressEvent(self, event) -> None:
        """按下即翻转，不等松开 —— **但只在命中区（自绘轨道）内才算**。

        以前跟着 QCheckBox 默认走「松开才切换」：用户从按下到松开通常
        100~200ms，再叠加 190ms 的滑块动画，手感就是"按下去半天没反应"
        （用户报"设置里的拨动开关按下有延迟，和别的开关不一样"—— 实测
        对照开关/定制开关/行标签三条路径全部只在松开瞬间翻转，探针
        devtools/_probe_toggle_timing.py）。改成**按下瞬间**就翻转：
        反馈提前到指尖落下的那一刻，所有 Switch 一致跟手。

        ⚠️⚠️ 这里必须自己过一遍 ``hitButton()``：覆写了 mousePressEvent 就
        **绕开了 QAbstractButton 原本的命中判定**，不加这一句的话控件矩形内
        任何位置按下都会翻转 —— 而开关控件比轨道宽得多（它横向占满整行的
        字段列，实测 381px vs 轨道 40px），于是"停在开关同一高度随便点一下
        就切换"（用户 2026-10-12 再次点名，就是这个原因：hitButton 写得再对
        也没人调它）。

        注意这里不能调 super()：QAbstractButton 的按下处理会把 release
        记成一次"点击"，松开时会再翻转一次（等于翻回去）。自己翻转完
        直接 accept，release 落下来时 down 状态没置位，不会再触发切换。
        """
        if (event.button() == Qt.MouseButton.LeftButton and self.isEnabled()
                and self.hitButton(event.position().toPoint())):
            self.setChecked(not self.isChecked())
            event.accept()
            return
        super().mousePressEvent(event)

    def _on_toggled(self, checked: bool) -> None:
        self._knob_anim.start(self._t, 1.0 if checked else 0.0)

    def _on_value(self, value) -> None:
        self._t = float(value)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        enabled = self.isEnabled()
        track = QRectF(0.0, (self.height() - self.TRACK_H) / 2.0, self.TRACK_W, self.TRACK_H)
        radius = self.TRACK_H / 2.0
        path = QPainterPath()
        path.addRoundedRect(track, radius, radius)

        off = switch_off_color()
        on = QColor(SWITCH_ON) if enabled else qcolor("line_strong")
        painter.fillPath(path, mix(off, on, self._t))

        # 圆点恒为白色（用户指定），两套主题一致。
        # 浅色主题下白点压在浅灰轨道上会有点"陷进去"，所以补一圈极淡的描边
        # 给它一点边界，这是开关的常规处理。
        knob_r = radius - 2.5
        cx = track.left() + radius + (track.width() - self.TRACK_H) * self._t
        cy = track.center().y()
        knob = QRectF(cx - knob_r, cy - knob_r, knob_r * 2, knob_r * 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(SWITCH_KNOB) if enabled else qcolor("surface_2"))
        painter.drawEllipse(knob)
        if enabled:
            ring = QPen(QColor(15, 18, 22, 34))
            ring.setWidthF(1.0)
            painter.setPen(ring)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(knob.adjusted(0.5, 0.5, -0.5, -0.5))

        if self.text():
            painter.setPen(qcolor("ink") if enabled else qcolor("ink_disabled"))
            painter.setFont(self.font())
            painter.drawText(
                QRectF(self.TRACK_W + self.GAP_PX, 0.0,
                       self.width() - self.TRACK_W - self.GAP_PX, float(self.height())),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())


# ------------------------------------------------------------------ 滑杆

class GlassSlider(QSlider):
    """毛玻璃强度滑杆：轨道与滑块**完全自绘**。

    为什么不用 QSS 画：QSS 的滑杆子控件定位是隐式的 —— 真机（2x 缩放）实测
    groove 被默认定位缩进了 ~17px，而滑块的滑动范围仍从控件左缘起算，
    强度在两端时圆点整个悬在轨道外面（用户报"滑轨显示异常仍未解决"）。
    与其跟这套玄学规则纠缠，不如和 Switch 一样自己画：几何全部自己算，
    任何缩放下都不会错位。

    交互：点击轨道即跳到该值，按住拖动连续调；滚轮不改数值
    （与 ChevronSpinBox/ChevronComboBox 同一条铁律 —— 避免误操作）。
    """

    TRACK_H = 4.0
    KNOB_D = 15.0

    def __init__(self, orientation=Qt.Orientation.Horizontal,
                 parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # -- 几何 ------------------------------------------------------------
    def _span(self) -> float:
        """滑块中心可移动的距离（轨道两端各留出半个滑块半径）。"""
        return max(1.0, self.width() - self.KNOB_D)

    def _knob_cx(self) -> float:
        rng = self.maximum() - self.minimum()
        frac = ((self.value() - self.minimum()) / rng) if rng > 0 else 0.0
        return self.KNOB_D / 2.0 + self._span() * frac

    def _value_at(self, x: float) -> int:
        frac = (x - self.KNOB_D / 2.0) / self._span()
        frac = 0.0 if frac < 0.0 else 1.0 if frac > 1.0 else frac
        rng = self.maximum() - self.minimum()
        return int(round(self.minimum() + rng * frac))

    # -- 绘制 ------------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cy = self.height() / 2.0
        # 轨道（全宽、浅灰）与已填充段（左段、墨色）
        track = QRectF(0.0, cy - self.TRACK_H / 2.0, self.width(), self.TRACK_H)
        path = QPainterPath()
        path.addRoundedRect(track, self.TRACK_H / 2.0, self.TRACK_H / 2.0)
        painter.fillPath(path, qcolor("line_strong"))
        knob_cx = self._knob_cx()
        if knob_cx > self.KNOB_D / 2.0:
            filled = track.adjusted(0.0, 0.0, knob_cx - self.width(), 0.0)
            fill_path = QPainterPath()
            fill_path.addRoundedRect(filled, self.TRACK_H / 2.0, self.TRACK_H / 2.0)
            painter.fillPath(fill_path, qcolor("ink"))

        # 滑块：墨色圆（QSS 时代的同款配色），一圈底色描边把圆从轨道上托出来
        radius = self.KNOB_D / 2.0
        knob = QRectF(knob_cx - radius, cy - radius, self.KNOB_D, self.KNOB_D)
        if self.isEnabled():
            painter.setPen(QPen(qcolor("surface"), 2.0))
            painter.setBrush(qcolor("ink"))
        else:
            painter.setPen(QPen(qcolor("surface"), 2.0))
            painter.setBrush(qcolor("ink_disabled"))
        painter.drawEllipse(knob)

    # -- 交互 ------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._value_at(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.setValue(self._value_at(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def wheelEvent(self, event) -> None:
        event.ignore()      # 滚轮不许改数值（用户点名"避免误操作修改数值"）


# ------------------------------------------------------------------ 静态图标

class IconLabel(QLabel):
    """纯展示的图标（不响应点击）。字形与 ``IconButton`` 同源，所以两者摆在一起时
    大小和粗细是一致的 —— 用文字符号画图标是干不出这个效果的。"""

    def __init__(self, glyph: str, px: int = 14,
                 parent: QWidget | None = None) -> None:
        super().__init__(glyph, parent)
        self.setFont(icon_font(px))
        self.setObjectName("iconLabel")


# ------------------------------------------------------------------ 标签 + 控件

class LabeledField(QWidget):
    """把「标签 + 控件」绑成一行。

    存在的理由只有一个：让「编码」「历史」这类**带标签的下拉框**能作为一个整体
    参与显隐。只藏下拉框会剩一个孤零零的标签挂在选项行里，比不藏还难看；
    逐个 setVisible 又会让调用方（设置面板、主窗口）都要记住谁跟着谁。
    绑成一个控件之后，显隐逻辑和那些开关完全一样。
    """

    def __init__(self, text: str, field: QWidget, spacing: int = 10,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.field = field
        self._text = text
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(spacing)
        self.label = QLabel(text)
        self.label.setFont(ui_font(10.5))
        row.addWidget(self.label)
        row.addWidget(field)

    def label_text(self) -> str:
        """控件上显示的名字。设置面板给的显示名要和它一致。"""
        return self._text


# ------------------------------------------------------------------ 搜索框

class SearchEdit(QLineEdit):
    """接收区搜索框：回车 = 下一个，Shift+回车 = 上一个。

    这两件事必须在 ``keyPressEvent`` 里拦，不能靠 ``returnPressed`` ——
    后者对 Shift+回车 一样会发，根本区分不出方向。

    Esc 不在这里管：它由主窗口那条窗口级快捷键统一处理。
    ``QShortcut`` 是在按键送到焦点控件**之前**被匹配掉的，写在这里收不到。
    """

    next_requested = Signal()
    prev_requested = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            (self.prev_requested if shift else self.next_requested).emit()
            return
        super().keyPressEvent(event)


# ------------------------------------------------------------------ 下拉框箭头

def _draw_chevron(painter: QPainter, rect: QRectF, glyph: str, px: int,
                  color: QColor) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setPen(color)
    painter.setFont(icon_font(px))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)
    painter.restore()


class _PopupItemDelegate(QStyledItemDelegate):
    """弹层条目的绘制：圆角内嵌色块 + 内缩文字。

    **为什么要自己画**（2026-10-15 用户截图点名"这种难看的 UI"）：
    QComboBox 弹层里 ``QComboBox QAbstractItemView::item`` 那套 QSS 规则在
    Fusion 上是**失效**的 —— 逐项试过 9 个开关（resizeMode / sizeAdjustPolicy /
    uniformItemSizes / 视图级样式表…）都不动，去掉 ``selection-background-color``
    也没用：选中态始终由样式引擎画成"顶到两边、方角、比行还高"的矩形，
    正是截图里那条丑东西。所以条目绘制改由这里接管：色块自己画成圆角 + 内缩，
    和界面其它圆角元素同一套语言（半径、内边距都取自 ui_theme 的 token）。
    """

    # 条目左右内边距：文字与色块左缘的距离（和卡片内边距一脉相承）
    PAD_X = 10
    # 色块相对条目四周的内缩：留出这一圈，色块才是"嵌在列表里"而不是"铺满一行"
    CHIP_INSET_X = 4
    CHIP_INSET_Y = 3
    CHIP_RADIUS = 8
    # 行高（逻辑像素）。比原先 QSS 撑出来的 30 略收一点，列表更紧凑。
    ROW_H = 28

    # 条目右侧的删除叉（只在 ``set_item_delete_enabled(True)`` 的下拉上启用，
    # 例如「车号」：用户 2026-10-22 要"车号展开后车号数字后面一个删除按钮(X)"）。
    # 宽度要同时用于**绘制**和**命中判定**（见 ChevronComboBox.eventFilter），
    # 两边必须用同一个常量，否则点得中看不见 / 看得见点不中。
    DELETE_W = 30
    DELETE_PX = 12

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.delete_enabled = False

    def sizeHint(self, option, index) -> QSize:      # noqa: N802 (Qt 命名)
        height = max(self.ROW_H, option.fontMetrics.height() + 12)
        return QSize(0, height)

    def paint(self, painter: QPainter, option, index) -> None:
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected or hovered:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            # 选中比悬停重一点：两者会同时为真（鼠标就在选中项上），取更亮的那个
            painter.setBrush(qcolor("sel", 30 if selected else 18))
            box = option.rect.adjusted(self.CHIP_INSET_X, self.CHIP_INSET_Y,
                                       -self.CHIP_INSET_X, -self.CHIP_INSET_Y)
            painter.drawRoundedRect(QRectF(box), self.CHIP_RADIUS, self.CHIP_RADIUS)
            painter.restore()

        painter.save()
        painter.setPen(qcolor("ink"))
        painter.setFont(option.font)
        right_pad = self.PAD_X + (self.DELETE_W if self.delete_enabled else 0)
        text_rect = option.rect.adjusted(self.PAD_X, 0, -right_pad, 0)
        # 超长条目（蓝牙设备那串 "MAC — 名字 — 信号 — 负载"）在屏幕宽度封顶后
        # 必须省略，否则文字会被视口硬裁成半个字。
        text = option.fontMetrics.elidedText(str(index.data() or ""),
                                             Qt.TextElideMode.ElideRight,
                                             max(0, text_rect.width()))
        painter.drawText(text_rect,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         text)
        painter.restore()

        if self.delete_enabled and self._deletable(index):
            self._paint_delete(painter, option)

    @staticmethod
    def _deletable(index) -> bool:
        """这一条能不能删：条目 data 非空才算（「（未选择）」data 是空串）。

        ⛔ 不能"开着 delete 就每条都画 ×"：占位行不是档案，点上去只会得到一个
        什么都不做的确认框（第一版实测就是这样，看着像坏了）。
        """
        return bool(index.data(Qt.ItemDataRole.UserRole))

    def _paint_delete(self, painter: QPainter, option) -> None:
        """画条目右端的「×」：悬停条目上更亮一档，暗示"这里能点"。"""
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = QRectF(option.rect.right() - self.DELETE_W + 4,
                      option.rect.top(), self.DELETE_W - 8, option.rect.height())
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setPen(qcolor("ink_2" if hovered else "ink_3"))
        painter.setFont(icon_font(self.DELETE_PX))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, GLYPH_CLOSE)
        painter.restore()


# 弹层离屏幕边缘至少留这么多（逻辑像素）：弹层是独立顶层窗口，贴在窗口右侧的
# 下拉（蓝牙设备那一栏）按最宽条目算出来的宽度会顶出屏幕右缘。
_POPUP_SCREEN_MARGIN = 12


class ChevronComboBox(QComboBox):
    """下拉框。箭头用系统图标字体自绘的 chevron，不用 QSS 的三角。

    原因见 ui_theme 里 ``QComboBox::down-arrow`` 的注释：QSS 靠"零尺寸 + 彩色边框"
    拼三角，Fusion 下常被挤成一个小方块，看着毛糙。自绘 chevron 与界面其它图标同源，
    主题跟随、高 DPI 下也锐利。原生箭头已在 QSS 里关掉，这里只补画一个。

    弹层条目由 :class:`_PopupItemDelegate` 接管绘制（QSS 的 ``::item`` 在 Fusion
    上失效，见那边的说明）。
    """

    ARROW_W = 24
    GLYPH_PX = 11
    # 条目右端的「×」被点击：(条目文字)。只在 set_item_delete_enabled(True) 时发。
    delete_requested = Signal(str)
    # 可编辑时内部那层 QLineEdit 的目标 x。Qt 的 QSS 不支持负 margin，样式表
    # 挪不动它，只能像 ChevronSpinBox 那样在事件过滤器里摆。
    # 目标是让**墨迹左边缘**和不可编辑的下拉框（由 QSS padding-left(12px)+边框
    # 直接画、落在 13px）齐平。可编辑的文字在 QLineEdit 行盒里另带约 2px 的
    # 内部左移，实测 edit.x=11 时墨迹正好落在 13px（2026-10-12 逐像素标定，
    # 探针 devtools/_probe_ink_all.py）。⚠️ 改字号/字体后要重新量。
    EDIT_TARGET_X = 11

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._placing_edit = False
        # 弹层展开动画（见 _animate_popup_open）；用到时才建 Tween
        self._popup_tween: Tween | None = None
        # 条目绘制的 delegate **惰性**创建（见 _ensure_popup_delegate）：
        # 在 __init__ 里碰 self.view() 会提前把弹层容器窗口建出来，凭空多出
        # 一堆隐藏顶层窗口 —— 实测会改变窗口管理器对主窗口的行为（全屏往返后
        # 位置被夹到 (0,0)），所以第一次弹出时才装。
        self._item_delegate: _PopupItemDelegate | None = None
        self._item_delete = False
        self._popup_target = None
        self._popup_below = True
        # 非编辑态的文字垂直自适应（见 _fit_text_vertical）：
        # _fit_shift = 当前实例 padding 造成的文字位移（正 = 下移，0 = 全局基准）；
        # _fit_key = 已经量过并稳定的"字体+文字+尺寸+dpr"组合，避免重复抓图。
        self._fit_shift = 0
        self._fit_key = None
        self.currentTextChanged.connect(lambda _text: self._place_edit())
        edit = self.lineEdit()
        if edit is not None:
            edit.installEventFilter(self)

    def setEditable(self, editable: bool) -> None:
        """⚠️ 过滤器必须挂在**这里**再补一次。

        ``lineEdit()`` 在构造时还不存在 —— 调用方是构造完才 ``setEditable(True)``
        的，所以 __init__ 里那次 install 拿不到编辑框，后续 Move/Resize 全都收
        不到，_place_edit 永远不会跑（实测：x 一直停在 13）。
        另外把编辑框的 textChanged 也接上：换端口这类"文字变了但控件没动"的
        情况不会有 Move/Resize，不接就永远摆不上（串口选择框踩过）。
        """
        super().setEditable(editable)
        edit = self.lineEdit()
        if editable and edit is not None:
            edit.installEventFilter(self)
            edit.textChanged.connect(lambda _text: self._place_edit())
            self._place_edit()

    def _place_edit(self) -> None:
        """把文字摆到位 —— 编辑态摆内层编辑框，非编辑态调实例 padding。

        编辑态：x 对齐左端（见 EDIT_TARGET_X）、y 按**实测墨迹**垂直居中
        （见 _edit_ink_offset）；事件过滤器里每次 Move/Resize 纠一次，样式摆
        回去就再纠回来。
        非编辑态：文字由 QStyleSheetStyle 画，挪不动它 —— 改为**改本控件的
        padding**把内容区整体推到位（见 _fit_text_vertical）。
        ⚠️ 防重入同 ChevronSpinBox：量墨迹的 grab() 会激活布局、再触发本函数。
        """
        if self._placing_edit or self.height() <= 0:
            return
        self._placing_edit = True
        try:
            if self.isEditable():
                self._place_editable()
            else:
                self._fit_text_vertical()
        finally:
            self._placing_edit = False

    def _place_editable(self) -> None:
        edit = self.lineEdit()
        if edit is None:
            return
        x = self.EDIT_TARGET_X
        y = edit.y()
        offset = _edit_ink_offset(edit)
        if offset is not None:
            y = round(self.height() / 2.0 - offset)
            y = max(0, min(self.height() - edit.height(), y))
        if edit.x() != x or edit.y() != y:
            edit.move(x, y)

    def _fit_text_vertical(self) -> None:
        """非编辑态：按**实测墨迹**给本控件算一条竖直居中的 padding。

        不可编辑下拉框的文字由 QStyleSheetStyle 画在内容区正中 —— 行盒居中
        不等于字形居中（下伸部空着，字看着偏下；x 高度的词如 "none" 偏得更多），
        而且偏多少**逐条文字不同**，全局 QSS 给不出一个对所有人都对的值。
        所以量一条、改一条：padding 上下各挪半格（和=12 不变、框高不变），
        把内容区整体推到墨迹居中为止。

        ⚠️ 必须把**当前 padding 已经造成的位移**（_fit_shift）从测量里扣掉再算
        目标，否则下一次进来会把自己刚矫正过的结果当成"本来就居中"再改回去，
        每轮 resize 来回抖（踩过）。
        """
        text = self.currentText()
        dpr = self.devicePixelRatioF() or 1.0
        key = (self.font().key(), text, self.width(), self.height(),
               round(dpr, 2))
        if key == self._fit_key:
            return                    # 这个组合量过且已稳定，别再抓图
        offset = _combo_label_ink_offset(self)
        if offset is None:
            return
        base_delta = (offset - self.height() / 2.0) - self._fit_shift
        shift = int(round(-base_delta))        # 目标位移（正 = 下移）
        shift = max(-4, min(4, shift))
        self._fit_key = key
        if shift == self._fit_shift:
            return
        self._fit_shift = shift
        # 基准（全局 QSS）是上下 6/6；位移 s 要把上下各挪 s：上 6+s、下 6-s
        top = 6 + shift
        bottom = 6 - shift
        self.setStyleSheet(
            f"QComboBox {{ padding: {top}px 10px {bottom}px 12px; }}")

    def resizeEvent(self, event) -> None:
        # 宿主先 resize，编辑框的坐标才是最终值 —— 这里再摆一次（缓存后很便宜）
        super().resizeEvent(event)
        self._place_edit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._place_edit()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.lineEdit() and self.isEditable() \
                and event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            self._place_edit()
        # 弹层条目的「×」：按下就吞掉这次点击（不改选中、不留弹层），
        # 交给调用方去弹二次确认。命中区域必须和委托绘制用同一个宽度。
        if self._item_delete and event.type() == QEvent.Type.MouseButtonPress:
            view = self.view()
            if view is not None and obj is view.viewport():
                pos = event.position().toPoint()
                index = view.indexAt(pos)
                if index.isValid() and _PopupItemDelegate._deletable(index):
                    rect = view.visualRect(index)
                    if pos.x() >= rect.right() - _PopupItemDelegate.DELETE_W + 1:
                        # ⛔ 取 UserRole（= 档案名），不是显示文本：
                        #    车号下拉显示的是"E74"，档案名是"E74-2026"。
                        text = str(index.data(Qt.ItemDataRole.UserRole) or "")
                        self.hidePopup()
                        self.delete_requested.emit(text)
                        return True
        return super().eventFilter(obj, event)

    def showPopup(self) -> None:
        # 弹出列表默认是一个**不透明的矩形顶层窗口**——QSS 给视图画的圆角会被
        # 窗口直角边缘整个裁掉，看起来就是一块直角板（用户点名"缺少圆角"）。
        # 把容器窗口改成无边框 + 半透明，视图自己画的圆角才圆得起来；
        # 视图四周的空隙由 QSS 的 margin 留出（半透明处透出桌面/底色）。
        container = self.view().window()
        container.setWindowFlags(Qt.WindowType.Popup
                                 | Qt.WindowType.FramelessWindowHint
                                 | Qt.WindowType.NoDropShadowWindowHint)
        container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._ensure_popup_delegate()
        self._fit_popup_width()
        super().showPopup()
        # ⚠️ 非编辑下拉的弹层容器布局里带「上下弹簧 + 底部箭头指示条」
        # （Qt 6.12 私有装饰，编辑下拉这几项是 0 高）—— 5+10+5 的黑条垫在
        # view 下面，QSS 圆角被裁成方角，观感跟波特率完全两样（用户点名：
        # 停止位/数据位/校验位/编码/主题/发送历史不一致，根因就是它）。
        # 不能只收缩容器（布局最小尺寸会把窗口顶回去，动画也会失效），
        # 要把布局里**非 view 的项全部压成 0**，两种下拉的布局就对齐了。
        view = self.view()
        lay = view.window().layout()
        if lay is not None:
            for i in range(lay.count()):
                it = lay.itemAt(i)
                w = it.widget()
                if w is not None and w is not view:
                    w.setFixedHeight(0)
                elif it.spacerItem() is not None:
                    it.spacerItem().changeSize(0, 0)
            lay.invalidate()
            lay.activate()
        # ⚠️ **立刻**摆到我们算的位置：Qt 上面那一下已经把弹层显示在它自己的
        #    位置上了（默认对齐当前项），不马上搬走的话，用户会看到"先出现在
        #    下方、再跳到上方"（保留行数那个下拉就这么被点名过）。
        #    动画仍延后一拍起跑（要等 Qt 定完位），但那时位置已经是对的。
        target, below = self._popup_place()
        self._popup_target = target
        self._popup_below = below
        container.setGeometry(target)
        # ⛔ 立刻进入"待展开"状态：先缩到起始高度、透明度归零，**再**返回事件循环。
        #    以前这两步在 _start_popup_anim（延后一拍）里做，于是从 showPopup 返回到
        #    那一拍之间，弹层是**满高且不透明**的 —— 用户看到"先整块闪一下、再缩回去
        #    重新长出来"（逐帧实测：19ms 时 h=150/opacity=1.00，30ms 才变成 h=14/0.28）。
        #    延后一拍仍然必须保留（要等 Qt 定完位），但"别让人看见"这一步不能等。
        start_h = max(14, int(target.height() * 0.25))
        if below:
            container.setGeometry(target.x(), target.y(), target.width(), start_h)
        else:
            container.setGeometry(target.x(), target.y() + target.height() - start_h,
                                  target.width(), start_h)
        container.setWindowOpacity(0.0)
        self._animate_popup_open()

    def _ensure_popup_delegate(self) -> None:
        """首次弹出时装上条目 delegate（惰性：别在构造期惊动弹层窗口）。"""
        if self._item_delegate is None:
            view = self.view()
            self._item_delegate = _PopupItemDelegate(view)
            self._item_delegate.delete_enabled = self._item_delete
            view.setItemDelegate(self._item_delegate)
            # 「×」要能点中：弹层条目由委托画，命中判定得自己接鼠标。
            # 过滤器挂在 viewport 上（条目区域就是它），并且开鼠标跟踪 ——
            # 否则悬停时条目不会重绘，× 永远是暗的、看不出可点。
            view.viewport().installEventFilter(self)
            view.viewport().setMouseTracking(True)

    def set_item_delete_enabled(self, enabled: bool) -> None:
        """打开"每个条目右端有个 ×，点了删这一条"的模式（「车号」下拉用）。

        用户 2026-10-22：车号展开后，数字后面要一个删除按钮（X），删除要二次确认。
        二次确认由**调用方**在 ``delete_requested`` 里做（控件只管"点中了哪一条"）。
        """
        self._item_delete = bool(enabled)
        if self._item_delegate is not None:
            self._item_delegate.delete_enabled = bool(enabled)
        if enabled:
            view = self.view()
            if view is not None:
                view.viewport().installEventFilter(self)
                view.viewport().setMouseTracking(True)

    def _fit_popup_width(self) -> None:
        """弹层宽度按**最宽的选项**自适应（下限是控件自身宽度）。

        弹层默认跟控件同宽 —— 控件窄的时候选项被截成 "l...1"（编码框实测），
        而波特率的弹层因为控件宽看着就舒服。用户点名"所有的都要像波特率
        一样展开"：量一遍最宽项，给视图一个够用的最小宽度。
        """
        view = self.view()
        fm = view.fontMetrics()
        widest = max((fm.horizontalAdvance(self.itemText(i))
                      for i in range(self.count())), default=0)
        # 弹层**只按需要变宽**：够放下"最宽选项 + 委托左右的文字内边距"就行。
        # 这里原来写死 +84（那还是条目由 QSS 画、外边距 12px 时的旧余量），
        # 条目改由 _PopupItemDelegate 画、QSS margin 归零之后它就明显偏宽了
        # —— 编码框只有 92 宽，弹层却撑到 121（用户点名"这里宽了"）。
        # 视口两侧内缩 + 竖直滚动条 + 边框的余量按 28 给，实测够用。
        need = widest + _PopupItemDelegate.PAD_X * 2 + 28
        # 条目右端带 × 的下拉（车号）要多留一格，否则占位/短条目被 × 挤成省略号。
        # ⚠️ 必须和委托绘制、命中判定用同一个常量，三处一起改。
        if self._item_delete:
            need += _PopupItemDelegate.DELETE_W
        # ⛔ 还要受**屏幕宽度**约束：蓝牙设备条目是"MАC — 名字 — 信号 — 负载"
        #    一长串，按最宽项算出来能到 570px，控件又贴在窗口右侧 —— 弹层右半边
        #    直接跑到屏幕外（用户点名"部分显示到屏幕外"）。这里先按屏幕封顶，
        #    超长条目由委托**省略号截断**（见 _PopupItemDelegate.paint）。
        screen_area = self.screen().availableGeometry()
        need = min(need, max(160, screen_area.width() - _POPUP_SCREEN_MARGIN * 2))
        view.setMinimumWidth(max(self.width(), need))

    def _animate_popup_open(self) -> None:
        """起展开动画 —— **推迟到下一轮事件循环**再真正开跑。

        为什么必须延后一拍：``super().showPopup()`` 返回时 Qt 还没把弹层放到最终
        位置 —— 空间不够要翻到控件上方、当前项要跟控件对齐，这些都是随后才调整的。
        此刻读到的 geometry 是旧位置，据此既会判错朝上/朝下，动画推的分帧几何又会
        和 Qt 的定位互相打架（实测数据位那个下拉：动画按朝下长、Qt 同时把它往上方
        挪，看着就是"打开方向跟别的下拉不一样"）。延后一个事件循环，读到的就是
        最终位置，方向判断与动画就都对齐了。
        """
        QTimer.singleShot(0, self._start_popup_anim)

    def _popup_place(self) -> tuple[QRect, bool]:
        """算出弹层**该在哪**（尺寸沿用 Qt 算好的，只改位置）。

        规则只有一条：**贴控件下方展开**；下方实在放不下（底栏那几个）才翻到
        控件上方。Qt 默认则会把"当前项"对齐到控件上（弹层压在控件头顶），
        于是有的下拉压在控件上、有的落在下方 —— 用户拿两张图对比说"明显不一样"。
        """
        container = self.view().window()
        geo = container.geometry()
        anchor = self.mapToGlobal(QPoint(0, 0))
        gap = 4
        below_top = self.mapToGlobal(QPoint(0, self.height())).y() + gap
        screen_area = self.screen().availableGeometry()
        # ⛔ 弹层是**独立顶层窗口**，Qt 只管把它摆到控件旁边，从不检查屏幕边界。
        #    蓝牙设备那种长条目会把弹层撑到 570px，控件又贴着窗口右缘 —— 右半边
        #    直接出屏（用户点名"部分显示到屏幕外"）。这里统一夹进屏幕可用区。
        width = min(geo.width(),
                    max(160, screen_area.width() - _POPUP_SCREEN_MARGIN * 2))
        x = min(anchor.x(), screen_area.right() + 1 - width - _POPUP_SCREEN_MARGIN)
        x = max(x, screen_area.left() + _POPUP_SCREEN_MARGIN)
        # 一律贴控件下方展开。下方空间不够时**压矮弹层**（列表自己滚），
        # 而不是翻到控件上方 —— 翻上去动画方向就反了，跟别的下拉不一致
        # （用户点名"保留行数那里打开方向有点问题"就是这个）。只有连两三行
        # 都放不下（底栏最下面那几个），才真的翻到上方。
        room = screen_area.bottom() - gap - below_top
        if room >= min(geo.height(), 120):
            return (QRect(x, below_top, width,
                          min(geo.height(), room)), True)
        above_room = anchor.y() - gap - screen_area.top() - _POPUP_SCREEN_MARGIN
        above_h = max(64, min(geo.height(), above_room))
        # ⚠️ 上边界也夹一次：只有"窗口自己顶部在屏外"时才用得上（正常走不到），
        #    但既然下面是 max(64, …)，不夹就可能把弹层顶出屏幕。
        above_y = max(screen_area.top() + _POPUP_SCREEN_MARGIN,
                      anchor.y() - gap - above_h)
        return QRect(x, above_y, width, above_h), False
                     # 翻到上方

    def _start_popup_anim(self) -> None:
        """弹层展开动画：从锚点一侧"展开"到完整高度，同时淡入。

        朝下开就从顶端往下长、朝上开就从底端往上长。150ms STANDARD 曲线，
        和界面其它动效同一套节拍（ui_anim 的共享计时器，不另起 timer）。
        中途被关掉就立刻还原，不再推帧。
        """
        # ⚠️ 这里**不做"弹层还在不在"的守卫**：延后一拍时窗口还没被标成可见，
        #    拿 isVisible() 当开关会把动画整个跳过（实测：七个下拉全都一帧到位）。
        #    弹层真被关了的话，往隐藏窗口推几何也无害，收尾统一交给 hidePopup。
        container = self.view().window()
        # ⛔ showPopup 返回前已经把容器缩到起始高度了（为了不闪），这里**不能**
        #    直接拿容器当前几何去量目标 —— 那量到的是"起始高度"，弹层会越开越矮
        #    （实测 150 → 37）。先把目标矩形还回去，再按最新尺寸/位置重算一次。
        if self._popup_target is not None:
            container.setGeometry(self._popup_target)
        # 位置再算一遍：延后这一拍里 Qt 可能又调过尺寸（比如按屏幕高度收了），
        # 用最新尺寸重算，顺便把位置再按一次（showPopup 里已摆过一次）。
        target, below = self._popup_place()
        self._popup_target = target
        self._popup_below = below
        start_h = max(14, int(target.height() * 0.25))
        if below:
            container.setGeometry(target.x(), target.y(), target.width(), start_h)
        else:
            container.setGeometry(target.x(), target.y() + target.height() - start_h,
                                  target.width(), start_h)
        container.setWindowOpacity(0.0)
        if self._popup_tween is None:
            self._popup_tween = Tween(150, STANDARD, self._on_popup_anim,
                                      parent=self)
        self._popup_tween.start(0.0, 1.0, 150)

    def _on_popup_anim(self, t: float) -> None:
        # ⚠️ 这里**不做任何"弹层还在不在"的守卫**：第一帧 tick 是同步发的，
        # 那一刻容器/view 的 isVisible() 都还没翻成 True，拿可见性当开关会把
        # 动画在第一帧就整个叫停（实测踩过：波特率弹层永远停在收起态）。
        # 弹层要是真被关了，往隐藏窗口推几何无害；收尾统一交给 hidePopup。
        if self._popup_target is None:
            return
        container = self.view().window()
        geo = self._popup_target
        # 高度从**起始比例**长到满高（起始 25% 与 showPopup 里摆的那一帧对齐）：
        # 以前按 h*global_t 算，t=0 那一帧会把 37px 直接缩到 14px —— 透明中看
        # 不出来，但逐帧采样里就是一次"先缩一下再长"的反向位移。
        k = 0.25 + 0.75 * t
        h = max(14, int(round(geo.height() * k)))
        if self._popup_below:
            container.setGeometry(geo.x(), geo.y(), geo.width(), h)
        else:
            container.setGeometry(geo.x(), geo.y() + geo.height() - h,
                                  geo.width(), h)
        container.setWindowOpacity(min(1.0, 0.35 + 0.65 * t))

    def hidePopup(self) -> None:
        if self._popup_tween is not None and self._popup_tween.running:
            self._popup_tween.stop()
        container = self.view().window()
        container.setWindowOpacity(1.0)
        # 半途关掉的话容器还停在裁剪高度 —— 不还原的话，下一次 showPopup 拿
        # "当前几何"当目标尺寸，弹层会越开越矮（复利式缩水）。
        if self._popup_target is not None:
            container.setGeometry(self._popup_target)
        super().hidePopup()

    def wheelEvent(self, event) -> None:
        """滚轮**不改变选中项** —— 下拉框基本都躺在可滚动区域里（设置面板的
        统一滚动区、右侧串口配置栏），鼠标滚过去时滚轮本该滚页面，落在这儿却会
        悄悄把"串口/波特率/编码"这类整机参数换掉，是纯粹的误操作。
        ``ignore()`` 把事件交还父级，外层该滚还滚。
        """
        event.ignore()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        rect = QRectF(self.width() - self.ARROW_W, 0.0,
                      float(self.ARROW_W), float(self.height()))
        color = qcolor("ink_2") if self.isEnabled() else qcolor("ink_disabled")
        _draw_chevron(painter, rect, GLYPH_COLLAPSE, self.GLYPH_PX, color)


class ChevronSpinBox(QSpinBox):
    """数字框。上下两个 chevron 同样自绘 —— 否则步进箭头会渲染成两颗小方块。

    另外这里要补一处文字内边距：全局 QSS 给 ``QSpinBox`` 写了 ``padding``，
    它对上下是生效的（数字竖直居中没问题），但对**左右不生效** ——
    数字会紧贴着左边框画出来，看着像压在线上。``QComboBox`` 那边没有这个毛病
    （它的行内编辑框会被 QSS 内缩），QSpinBox 的编辑框是样式直接摆的，不吃 padding。
    所以自己给编辑框设 textMargins，把左边让开、右边留出一点，别顶到 chevron 上。
    """

    ARROW_W = 22
    GLYPH_PX = 9
    # 左, 上, 右, 下 —— 只动**左右**（左让开边框，右避开箭头）。
    # ⚠️ 上下必须是 0：竖直位置由 _place_edit 按**实测墨迹**摆，再叠一层
    # top textMargin 就是双重修正（早先 top=4 实测偏低 5px，就是用户报的
    # "输入框内文字与上下边框间距不一致"）。
    # ⚠️ 左边 10 是为了和下拉框的**墨迹左边缘**（13px）齐平：QSpinBox 的
    # padding 对行内编辑框不生效、只能靠 textMargin 补，而墨迹又比笔位再右
    # 偏 2~3px（字形左边距）—— 2026-10-12 逐像素标定（_probe_ink_all.py）。
    TEXT_MARGIN = (10, 0, 6, 0)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._placing_edit = False
        self._edit = self.findChild(QLineEdit)
        if self._edit is not None:
            self._edit.setTextMargins(*self.TEXT_MARGIN)
            # 样式会把行内编辑框摆到偏上 1~2px 的位置（QSS padding 与缩放
            # 组合下的怪癖，用户点名"上下间距不相等"），而且**后续布局周期里
            # 还会再摆回去** —— 只在 resizeEvent 里挪一次压不住。给它挂一个
            # 事件过滤器：样式每挪一次，立刻纠正回来（y 收敛后是空操作）。
            self._edit.installEventFilter(self)

    def wheelEvent(self, event) -> None:
        """滚轮**不改数字**（用户点名："禁止通过鼠标滚轮增减输入框内的数字，
        避免误操作修改数值"）。数字框多数躺在滚动区里，滚一下就把"保留行数"
        "电压上下限"这类参数改掉，而且改了什么完全没有反馈。``ignore()``
        交给父级，页面该滚还滚。
        """
        event.ignore()

    def minimumSizeHint(self) -> QSize:              # noqa: N802 (Qt 命名)
        """显式设过最小宽度时，**以它为准**。

        ⛔ 不覆写这条，布局不会理 ``setFixedWidth``：QAbstractSpinBox 的
        minimumSizeHint 是按"最长数值 + 左右 padding + 箭头"算的（实测 78 宽的
        赛季框报 111），于是同一排里的控件集体外溢 —— 2026-10-22 把
        「赛季 / 车号 / 模组个数」并到一排时，加号直接被裁掉。
        """
        hint = super().minimumSizeHint()
        if self.minimumWidth() > 0:
            hint.setWidth(min(self.minimumWidth(), hint.width()))
        return hint

    def _place_edit(self) -> None:
        """把行内编辑框摆到让**文字墨迹**在控件里竖直居中的位置。

        ⛔ 这里**不用 grab 量墨迹**（早先的实现是抓图找墨迹中心）。抓图在真实
        运行路径里会失准：设置面板是"位图放大 → 换真面板"上屏的，控件在某些
        时刻 grab 到的不是最终状态，实测打包版里数字仍偏低 7px（用户两次反馈
        "距离上下边框不一样"）。现在改成**纯字体度量**，不依赖任何截图：

        - ``tightBoundingRect(text)`` 给出墨迹相对**基线**的上下界；
        - 行盒里基线在 ``ascent`` 处，于是墨迹中心在行盒里的偏移是
          ``ascent + (top+bottom)/2``；
        - 要墨迹中心对齐控件中心，行盒顶就该在 ``h/2 - 该偏移``；
        - 编辑框把行盒竖直居中，所以编辑框 y = 行盒顶 - (edit_h - line_h)/2。

        改了字号/字体也自动对（全部从当前 fontMetrics 算）。
        """
        if self._edit is None or self._placing_edit \
                or self.height() <= 0 or self._edit.height() <= 0:
            return
        self._placing_edit = True
        try:
            fm = self._edit.fontMetrics()
            text = self._edit.text()
            tight = fm.tightBoundingRect(text) if text else None
            if tight is not None and tight.height() > 0:
                ink_center = fm.ascent() + (tight.top() + tight.bottom() + 1) / 2.0
                line_h = fm.height()
                want_y = round(self.height() / 2.0 - ink_center
                               - (self._edit.height() - line_h) / 2.0)
            else:
                want_y = (self.height() - self._edit.height()) // 2
            want_y = max(0, min(self.height() - self._edit.height(), want_y))
            if self._edit.y() != want_y:
                self._edit.move(self._edit.x(), want_y)
        finally:
            self._placing_edit = False

    def resizeEvent(self, event) -> None:
        # 宿主先 resize，编辑框的坐标才是最终值 —— 这里再摆一次（缓存后很便宜）
        super().resizeEvent(event)
        self._place_edit()

    def showEvent(self, event) -> None:
        """显示后再补几拍纠正。

        ⛔ 只在构造 / resize 时摆一次不够：真实路径里（设置面板是"位图放大 →
        换真面板"上屏的）控件显示之后样式还会再摆一次编辑框，实测首帧 y=7、
        正确值 6，打包版里偏差能到 5px 以上（用户两次反馈"距离上下边框不一样"）。
        这里按 0/80/260/520ms 各纠正一次；位置收敛后都是空操作，开销可忽略。
        """
        super().showEvent(event)
        self._place_edit()
        for delay in (0, 80, 260, 520):
            QTimer.singleShot(delay, self._place_edit)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._edit and event.type() in (QEvent.Type.Move,
                                                  QEvent.Type.Resize):
            self._place_edit()
        return super().eventFilter(obj, event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        left = float(self.width() - self.ARROW_W)
        half = self.height() / 2.0
        color = qcolor("ink_2") if self.isEnabled() else qcolor("ink_disabled")
        _draw_chevron(painter, QRectF(left, 0.0, float(self.ARROW_W), half),
                      GLYPH_EXPAND_CHEVRON, self.GLYPH_PX, color)
        _draw_chevron(painter, QRectF(left, half, float(self.ARROW_W), half),
                      GLYPH_COLLAPSE, self.GLYPH_PX, color)


# ------------------------------------------------------------------ 会闪的数字/文字标签

class FlashLabel(QLabel):
    """会"亮一下"的标签 —— 底栏统计数字、接收区时钟用它。

    用户要的是"数字变化要有动效"。数字**滚动**（逐位插值）会有一个副作用：
    任何读 ``.text()`` 的地方都会拿到中间值（自测里就有"接收 4 字节"这条断言）
    —— 统计口径必须始终是**真值**。所以这里改成苹果 §1 的另一种做法：
    数字**立刻**更新为真值，同时让整行来一次很短的**高光衰减**（颜色从 ink_2
    提亮到 ink 再落回），变化瞬间被一眼看到，又不会让数值失真。

    衰减走 out_expo（瞬间亮起、余晖拖长），和卡片的描边闪烁同一套曲线语言。
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._flash = 0.0
        self._flash_anim = Tween(420, out_expo, self._on_flash, parent=self)

    def flash(self, strength: float = 1.0, duration: int | None = None) -> None:
        """亮一下。起点取**当前亮度**，连着闪是"接着亮"而不是"重新亮一下"。

        ``duration``：每次高光的时长（不给用默认 420ms）。时钟那种**每秒**都会
        触发的场合要短一点 —— 不然高光会一直挂着，既看不到"变化"又白白占用节拍。

        ⚠️ **不可见时不起动画**：关掉的窗口其时钟定时器仍在跑，若还去闪就会让
        动画节拍器一直转（自测里那条"补间都跑完了，调度器还在转"就是这么挂的）。
        看不到的控件本来也没必要动画 —— 这条同时是"静止时零开销"的一部分。
        """
        if not self.isVisible():
            return
        self._flash_anim.start(min(1.0, max(self._flash, strength)), 0.0,
                               duration=duration)

    def _on_flash(self, value) -> None:
        self._flash = float(value)
        self.update()

    def paintEvent(self, event) -> None:
        text = self.text()
        if not text:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        # 常态色取标签自身的调色板前景色（这样"灰调"仍由主题控制），
        # 闪的时候往 ink（主文字色）提亮 —— 提亮方向与"变化=值得注意"一致。
        base = self.palette().color(self.foregroundRole())
        color = mix(base, qcolor("ink"), self._flash)
        painter.setPen(color)
        painter.setFont(self.font())
        margins = self.contentsMargins()
        rect = QRectF(self.rect()).adjusted(margins.left(), margins.top(),
                                            -margins.right(), -margins.bottom())
        painter.drawText(rect, self.alignment(), text)


# ------------------------------------------------------------------ 活动指示

class PulseDot(QWidget):
    """状态灯，两种模式：

    · **活动**（默认）：有数据流过时亮起并按非线性曲线衰减，让「正在通信」看得见。
      黑白主题下用「亮度 + 尺寸」表达强度，不靠颜色。衰减给到 560ms、125Hz ——
      数据一密就不断被重新点亮，看着是连续在闪，而不是一格一格地跳。
    · **忙碌**（``set_busy(True)``）：连接中，画一支**矢量弧** —— 角度匀速转、
      弧长按非线性曲线呼吸（长→短→长）。这是"正在干活"最标准的矢量表达，
      而且用的是同一颗灯：它就在状态文字左边，连接时本来就闲着，
      切个模式即可，底栏布局一个字节都不用动。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._level = 0.0
        self._busy = False
        self._spin_t = 0.0
        self.setFixedSize(18, 18)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # 数据脉冲的衰减用指数曲线（out_expo）：亮得快、余晖长，数据一密
        # 就叠成"一直在微微亮"的呼吸感，而不是每次都被硬切成方波。
        self._pulse_anim = Tween(560, out_expo, self._on_value, parent=self)
        # 旋转：**线性**一圈 1 秒（转速必须匀，转了才知道"在转"），重复无限。
        # 弧长的呼吸感由绘制那边用非线性曲线叠上去。
        self._spin_anim = Tween(1000, linear, self._on_spin, parent=self, repeat=-1)

    def pulse(self, strength: float = 1.0) -> None:
        if self._busy:
            return                       # 连接中由弧表达，别再叠脉冲
        self._level = min(1.0, self._level + strength)
        self._pulse_anim.start(self._level, 0.0)

    def set_busy(self, busy: bool) -> None:
        """连接中 = True。切进/切出都立即重绘，不留残影。"""
        busy = bool(busy)
        if busy == self._busy:
            return
        self._busy = busy
        if busy:
            self._spin_t = 0.0
            self._spin_anim.start(0.0, 1.0)
        else:
            self._spin_anim.stop()
            self._spin_t = 0.0
            self._level = 0.0           # 忙碌期间没收过脉冲，退出时从静息起
        self.update()

    @property
    def busy(self) -> bool:
        return self._busy

    def _on_value(self, value) -> None:
        self._level = float(value)
        self.update()

    def _on_spin(self, value) -> None:
        self._spin_t = float(value)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QRectF(self.rect()).center()
        if self._busy:
            self._paint_busy(painter, center)
            return
        painter.setPen(Qt.PenStyle.NoPen)

        # 静息时是个淡灰点，有数据时变亮变大，够看得出「在通信」就行
        rest = qcolor("ink_3", 150)
        peak = qcolor("pulse")
        if self._level > 0.02:
            halo = 5.0 + 5.0 * self._level
            painter.setBrush(qcolor("pulse", int(38 * self._level)))
            painter.drawEllipse(center, halo, halo)

        dot_r = 3.2 + 1.8 * self._level
        painter.setBrush(mix(rest, peak, self._level))
        painter.drawEllipse(center, dot_r, dot_r)

    def _paint_busy(self, painter: QPainter, center: QRectF) -> None:
        """矢量弧：淡轨迹环 + 亮弧段。角度匀速、弧长非线性呼吸。"""
        radius = 5.6
        box = QRectF(center.x() - radius, center.y() - radius,
                     radius * 2.0, radius * 2.0)

        track = QPen(qcolor("ink_3", 80), 1.5)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(box)

        # 弧长：40° → 250° → 40°，三角波上再套一层 in_out_quint，
        # 所以"长得快、收得慢"，不是匀速伸缩（匀速伸缩像机械指针）。
        phase = (self._spin_t * 2.0) % 1.0
        tri = phase * 2.0 if phase < 0.5 else (1.0 - phase) * 2.0
        sweep = 40.0 + 210.0 * in_out_quint(tri)
        start = -90.0 + 360.0 * self._spin_t

        arc = QPen(qcolor("ink"), 1.8)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc)
        # drawArc 的角度单位是 1/16 度
        painter.drawArc(box, int(start * 16), int(sweep * 16))
