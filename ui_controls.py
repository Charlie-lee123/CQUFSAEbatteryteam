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

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFontMetrics, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider,
    QSpinBox, QWidget,
)

from ui_anim import Tween, out_cubic, out_quint
from ui_theme import (
    CAPTION_BTN_H, CAPTION_BTN_W, GLYPH_COLLAPSE, GLYPH_COPY,
    GLYPH_EXPAND_CHEVRON, SWITCH_KNOB, SWITCH_ON,
    icon_font, mix, qcolor, switch_off_color, ui_font,
)


# ------------------------------------------------------------------ 主按钮

class InkButton(QPushButton):
    """药丸按钮。primary 是墨色实底（主操作），ghost 是浅底描边（次操作）。"""

    def __init__(self, text: str = "", variant: str = "ghost",
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._variant = variant
        self._hover = 0.0
        self._press = 0.0
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
        self._press = 1.0
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._press = 0.0
        self.update()
        super().mouseReleaseEvent(event)

    def _animate_to(self, target: float, duration: int) -> None:
        self._hover_anim.start(self._hover, target, duration)

    def _on_hover(self, value) -> None:
        self._hover = float(value)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -1.0, -1.0)
        radius = rect.height() / 2.0          # 药丸
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        enabled = self.isEnabled()
        if self._variant == "primary":
            base = qcolor("fill_primary")
            hot = qcolor("fill_primary_hover")
            text_color = qcolor("on_primary")
        else:
            base = qcolor("surface_2")
            hot = qcolor("surface_hover")
            text_color = qcolor("ink")

        lit = max(self._hover, self._press)
        if not enabled:
            painter.fillPath(path, QColor(0, 0, 0, 0))
            pen = QPen(qcolor("line"))
            pen.setWidthF(1.0)
            painter.strokePath(path, pen)
            text_color = qcolor("ink_disabled")
        else:
            painter.fillPath(path, mix(base, hot, lit))
            if self._variant == "ghost":
                pen = QPen(mix(qcolor("line"), qcolor("line_strong"), lit))
                pen.setWidthF(1.0)
                painter.strokePath(path, pen)

        painter.setPen(text_color)
        painter.setFont(self.font())
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())


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
        self._press = 1.0
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._press = 0.0
        self.update()
        super().mouseReleaseEvent(event)

    def _animate_to(self, target: float, duration: int) -> None:
        self._hover_anim.start(self._hover, target, duration)

    def _on_hover(self, value) -> None:
        self._hover = float(value)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -1.0, -1.0)
        radius = self._radius()
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        enabled = self.isEnabled()
        lit = max(self._hover, self._press)
        if not enabled:
            glyph_color = qcolor("ink_disabled")
        elif self._active:
            # 激活态：淡蓝圆角底 + 细蓝描边 + 蓝字形 —— 压过悬停/按下的灰阶反馈，
            # 状态一眼可见（两竖换三角太小，用户反馈过"按了没反应"）
            tint = QColor(SWITCH_ON)
            tint.setAlpha(38)                 # 淡淡一层底，不做成实心色块
            painter.fillPath(path, tint)
            painter.setPen(QPen(QColor(SWITCH_ON), 1.0))
            painter.drawPath(path)
            glyph_color = QColor(SWITCH_ON)
        elif self._variant == "filled":
            base = qcolor("fill_primary")
            painter.fillPath(path, mix(base, qcolor("fill_primary_hover"), lit))
            glyph_color = qcolor("on_primary")
        else:
            # 平时完全透明，只有悬停/按下才浮出底色；
            # hover_fill=False 的（底栏齿轮）连这一层也不画，永远只留字形
            if self._hover_fill:
                resting = qcolor("surface_hover", 0)
                painter.fillPath(path, mix(resting, qcolor("surface_hover"),
                                           max(lit, 0.0) if lit > 0.01 else 0.0))
            glyph_color = mix(qcolor("ink_2"), qcolor("ink"), lit)

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


# ------------------------------------------------------------------ 行内复制图标

class CopyChip(QWidget):
    """接收区里悬停时浮出的小图标：复制这一条接收内容。

    造型与 ``IconButton`` 同款 —— 同样只画一个字形、同样的圆角比例。**只有图标，
    不带文字**：它跟着鼠标在日志里挪，占的横面积越小越不挡事，而"两张纸"这个
    字形在"鼠标正悬在某条日志上"的语境里没有歧义。

    与 IconButton 的唯一差别是**它给自己铺了一层近乎不透明的面色**：IconButton
    底下是干净的卡面，透过去无所谓；它悬在日志文字上，透过去就是字叠字。

    它**不接收鼠标事件**（``WA_TransparentForMouseEvents``），点击由接收区的事件
    过滤器代为判定。这不是偷懒 —— 图标是浮在文本区 viewport 上的子控件，一旦它
    接受鼠标事件，鼠标移上去时底下的文本区就会收到 Leave，图标会当场消失，
    根本点不到。所以它只负责"被看见"（配合淡入与描边），"被点"交给宿主。

    出现时走 ``ui_anim`` 的 120fps 非线性淡入：它跟着鼠标在日志里挪动，
    一帧跳出来会很扎眼；已经在亮着时不重新点亮，否则高速移动中会不停闪。
    """

    SIZE = 24
    GLYPH_PX = 13

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lit = 0.0
        # 图标压在日志文字上方，必须"穿过"它才能选中下面的文字
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._glyph_font = icon_font(self.GLYPH_PX)
        self.setFixedSize(self.SIZE, self.SIZE)
        self._lit_anim = Tween(150, out_cubic, self._on_lit, parent=self)

    def appear(self) -> None:
        """浮出来时点亮一下；已经亮着就保持不动。"""
        if self._lit < 1.0:
            self._lit_anim.start(self._lit, 1.0)

    def reset(self) -> None:
        """收起来后复位，下次浮出重新淡入。"""
        self._lit_anim.stop()
        self._lit = 0.0
        self.update()

    def _on_lit(self, value) -> None:
        self._lit = float(value)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setOpacity(self._lit)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = self.width() * 0.26                 # 与 IconButton 同一套圆角比例
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        # 面色必须近乎不透明：它悬在日志文字上，透过去就看不清了。
        # 用 surface 而不是 surface_2 —— 浅色主题下前者是纯白，压在白底日志区上
        # 靠描边分层，正好是 Windows 那种浮层的感觉。
        painter.fillPath(path, qcolor("surface", 240))
        pen = QPen(mix(qcolor("line"), qcolor("line_strong"), self._lit))
        pen.setWidthF(1.0)
        painter.strokePath(path, pen)

        painter.setFont(self._glyph_font)
        painter.setPen(qcolor("ink_2"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, GLYPH_COPY)


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

    def hitButton(self, pos: QPoint) -> bool:
        """整个控件矩形都可点。

        ⚠️ QCheckBox 的默认命中区域按**原生勾选框**（Fusion 的 14px 小方框）
        算，跟这里自绘的 40px 胶囊轨道完全对不上 —— 点轨道中段、右半边、
        文字全都落在"点击区域"之外，表现就是"开关点了没反应"（真机实测：
        点轨道中点 hitButton=False，只有最左侧 14px 生效；用户报的
        "部分拨动开关无法点击或无法切换状态"就是它）。自绘控件的命中区域
        必须自己定义 —— 整个矩形都算。
        """
        return self.rect().contains(pos)

    def mousePressEvent(self, event) -> None:
        """按下即翻转，不等松开。

        以前跟着 QCheckBox 默认走「松开才切换」：用户从按下到松开通常
        100~200ms，再叠加 190ms 的滑块动画，手感就是"按下去半天没反应"
        （用户报"设置里的拨动开关按下有延迟，和别的开关不一样"—— 实测
        对照开关/定制开关/行标签三条路径全部只在松开瞬间翻转，探针
        devtools/_probe_toggle_timing.py）。改成**按下瞬间**就翻转：
        反馈提前到指尖落下的那一刻，所有 Switch 一致跟手。

        注意这里不能调 super()：QAbstractButton 的按下处理会把 release
        记成一次"点击"，松开时会再翻转一次（等于翻回去）。自己翻转完
        直接 accept，release 落下来时 down 状态没置位，不会再触发切换。
        """
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
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


class ChevronComboBox(QComboBox):
    """下拉框。箭头用系统图标字体自绘的 chevron，不用 QSS 的三角。

    原因见 ui_theme 里 ``QComboBox::down-arrow`` 的注释：QSS 靠"零尺寸 + 彩色边框"
    拼三角，Fusion 下常被挤成一个小方块，看着毛糙。自绘 chevron 与界面其它图标同源，
    主题跟随、高 DPI 下也锐利。原生箭头已在 QSS 里关掉，这里只补画一个。
    """

    ARROW_W = 24
    GLYPH_PX = 11

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
        super().showPopup()

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
    # ⚠️ 上下必须是 0：数字在框里的竖直位置**只由 QSS 的对称 padding 管**
    # （QLineEdit 会把行盒在内容区里居中），再叠一层 top textMargin 就是
    # **双重修正**。原来这里写的是 top=4（"墨迹补偿"），真机逐像素量出来
    # 数字上留白 11 / 下留白 6 —— 明显偏低，和下拉框（10.5 / 10）对不上，
    # 用户报的"各输入框内文字与上下边框的间距不一致"就是它。归零之后
    # 实测 9 / 8，与下拉框一致。
    TEXT_MARGIN = (11, 0, 6, 0)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edit = self.findChild(QLineEdit)
        if self._edit is not None:
            self._edit.setTextMargins(*self.TEXT_MARGIN)
            # 样式会把行内编辑框摆到偏上 1~2px 的位置（QSS padding 与缩放
            # 组合下的怪癖，用户点名"上下间距不相等"），而且**后续布局周期里
            # 还会再摆回去** —— 只在 resizeEvent 里挪一次压不住。给它挂一个
            # 事件过滤器：样式每挪一次，立刻纠正回垂直居中（y 收敛后是空操作）。
            self._edit.installEventFilter(self)

    def wheelEvent(self, event) -> None:
        """滚轮**不改数字**（用户点名："禁止通过鼠标滚轮增减输入框内的数字，
        避免误操作修改数值"）。数字框多数躺在滚动区里，滚一下就把"保留行数"
        "电压上下限"这类参数改掉，而且改了什么完全没有反馈。``ignore()``
        交给父级，页面该滚还滚。
        """
        event.ignore()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._edit and event.type() in (QEvent.Type.Move,
                                                  QEvent.Type.Resize):
            edit = self._edit
            want_y = (self.height() - edit.height()) // 2
            if edit.y() != want_y:
                edit.move(edit.x(), want_y)
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


# ------------------------------------------------------------------ 活动指示

class PulseDot(QWidget):
    """状态灯：有数据流过时亮起并按非线性曲线衰减，让「正在通信」看得见。

    黑白主题下用「亮度 + 尺寸」表达强度，不靠颜色。衰减给到 560ms、125Hz —— 
    数据一密就不断被重新点亮，看着是连续在闪，而不是一格一格地跳。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._level = 0.0
        self.setFixedSize(18, 18)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._pulse_anim = Tween(560, out_cubic, self._on_value, parent=self)

    def pulse(self, strength: float = 1.0) -> None:
        self._level = min(1.0, self._level + strength)
        self._pulse_anim.start(self._level, 0.0)

    def _on_value(self, value) -> None:
        self._level = float(value)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QRectF(self.rect()).center()
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
