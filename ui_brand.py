"""车队品牌要素：素材定位、按主题取队标、品牌页头。

素材来自车队 VIS（视觉识别系统），已本地化到项目 ``assets/`` —— 这个工具要拿到
赛场上去用，不能指望那时候还能连上车队内网。

按 VIS 规范取用：
- 用「横排中英文组合」，并按规范给出的**最小屏幕宽度 560px** 渲染（低于这个宽度
  组合里那行英文会小到看不清）。页头高度由组合的宽高比带出来。
- 按背景选版本：深色主题用深底版（反白字），浅色主题用浅底版（藏蓝字）。
- 组合自带中英文之间的短竖线，不要再另画分隔线。

**清晰度**：优先用矢量 SVG 渲染，位图 PNG 只作退路。
之前一直用 PNG 缩到 560 **逻辑**像素，而本机是 2 倍像素密度，Qt 会把这个
560px 的位图再放大两倍去画 —— 所以队标发虚。现在按设备像素比渲染
（560 逻辑 -> 1120 物理像素），矢量在多少 DPI 下都锐利。
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QFontMetricsF, QGuiApplication, QIcon, QImage, QPainter, QPen, QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from ui_controls import CaptionButton
from ui_theme import (
    CAPTION_GAP, CAPTION_H, CAPTION_INSET, CARD_PAD, GAP, GLYPH_WIN_CLOSE, GLYPH_WIN_MAX,
    GLYPH_WIN_MIN, GLYPH_WIN_RESTORE, HEADER_PAD, LOCKUP_MIN_W, SHADOW_PAD,
    current_scheme, qcolor, ui_font,
)

# 按背景选版本：深色主题用深底版，浅色主题用浅底版
LOCKUP_SVG = {"dark": "lockup_on_dark.svg", "light": "lockup_on_light.svg"}
LOCKUP_PNG = {"dark": "lockup_on_dark.png", "light": "lockup_on_light.png"}
FALLBACK_ASPECT = 1576.0 / 370.0

_cache: dict[tuple[str, int, int], tuple[QPixmap | None, float]] = {}
_CACHE_LIMIT = 8


def asset_dir() -> str:
    """素材目录。源码运行取项目目录；PyInstaller 打包后取解包出来的临时目录。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "assets")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def _render_svg(path: str, px_width: int, dpr: float) -> tuple[QPixmap | None, float]:
    renderer = QSvgRenderer(path)
    if not renderer.isValid():
        return None, FALLBACK_ASPECT
    size = renderer.defaultSize()
    aspect = size.width() / max(1, size.height())
    height = max(1, int(round(px_width / max(0.01, aspect))))
    image = QImage(px_width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap, aspect


def _render_png(name: str, px_width: int, dpr: float) -> tuple[QPixmap | None, float]:
    source = QPixmap(os.path.join(asset_dir(), name))
    if source.isNull():
        return None, FALLBACK_ASPECT
    aspect = source.width() / max(1, source.height())
    pixmap = source.scaled(px_width, 10_000, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap, aspect


def lockup_pixmap(scheme: str, logical_width: float,
                  dpr: float) -> tuple[QPixmap | None, float]:
    """取当前主题的队标组合。返回 (位图, 宽高比)。

    位图的 devicePixelRatio 已经设好，所以调用方直接按逻辑坐标画就行，
    Qt 会自动用上全部物理像素。
    """
    px_width = max(8, int(round(logical_width * max(1.0, dpr))))
    key = (scheme, px_width, int(dpr * 100))
    if key in _cache:
        return _cache[key]

    if len(_cache) >= _CACHE_LIMIT:
        _cache.clear()

    svg = os.path.join(asset_dir(), LOCKUP_SVG.get(scheme, LOCKUP_SVG["dark"]))
    result = _render_svg(svg, px_width, dpr) if os.path.exists(svg) else (None, FALLBACK_ASPECT)
    if result[0] is None:
        # 矢量不可用时退回位图：清晰度差一档，但至少画面不缺东西
        result = _render_png(LOCKUP_PNG.get(scheme, LOCKUP_PNG["dark"]), px_width, dpr)

    _cache[key] = result
    return result


def app_icon() -> QIcon:
    """窗口/任务栏图标：打包好的多尺寸 app.ico（纯白底 L3 超椭圆 + 竖排组合）。

    必须显式设给 QApplication —— PyInstaller 的 --icon 只管 **exe 文件本身**的图标
    （资源管理器里显示的那个），运行时窗口图标不会自动跟着走，
    不设的话标题栏和任务栏上就是 Qt 的默认占位图标。
    """
    path = os.path.join(asset_dir(), "app.ico")
    icon = QIcon(path)
    if icon.isNull():
        # 素材没打进包 / 被删了：界面上会**静默**少一枚图标（标题栏左边那块空着），
        # 光看界面不知道是绘制坏了还是图没了。日志里留一行，别让人靠猜。
        import app_log
        app_log.warning(f"窗口图标加载失败：{path} 不存在或不是有效的图标文件")
    return icon


class CaptionBar(QWidget):
    """自绘标题栏：右边三颗窗口键（最小化 / 最大化还原 / 关闭），左边一条可拖拽区。

    为什么要自己画：窗口改成**无边框**之后系统标题栏整个没有了 —— 只有这样，
    全屏时 shell 才认得出"这是个全屏应用"从而藏掉任务栏（用户既要盖任务栏、
    又要这三颗键一直都在，两者只有无边框 + 自绘这条路能同时满足）。
    实测：无边框 + showFullScreen，屏幕底部像素仍是页面底色，任务栏确实被藏住。

    **三颗键的位置在任何状态下都不变**（窗口普通态 / 全屏 / 接收区展开态）：
    这条控件不在 ``_expand_receive`` 会隐藏的那批卡片里，也不随卡片显示隐藏而
    移动，永远在标题栏右端同一条竖线上。

    拖拽走 ``startSystemMove()``（把 WM_NCLBUTTONDOWN/HTCAPTION 交给系统），
    所以拖到屏幕边缘的吸附、双击最大化这些系统行为都还在。全屏时不给拖 ——
    拖一个满屏窗口没有意义，用户还原请用中间那颗键或 Esc。
    """

    ICON_PX = 16
    ICON_GAP = 8            # 图标与标题文字之间的留白（断言里要认这一段）
    # 窗口图标 / 标题文字的左边距。
    # 标题栏整条**出血**到窗口左右边缘（见 main_window._build_ui），所以这里要把
    # 根布局原先那圈 ``GAP - SHADOW_PAD`` 的白边自己补回来，绝对值才和下面品牌
    # 页头里队标的左边缘对齐（那是"窗口白边 + 卡片内衬"两段相加）。
    # 抽成常量是给断言用的：自测按它量"图标画在哪一格"，写死字面量两边会漂。
    TITLE_LEFT = GAP - SHADOW_PAD + CARD_PAD + SHADOW_PAD

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(CAPTION_H)
        self.setObjectName("captionBar")
        self._title = title
        self._title_face = ui_font(11.5)
        # 窗口图标：系统标题栏被无边框去掉之后，原来钉在标题栏左端的那枚图标
        # 也跟着没了（用户："我的 logo 呢，logo 得保留啊"）。自己画回来 ——
        # QIcon 直接缓存住，别每帧从 .ico 里重新解一遍。
        self._icon = app_icon()

        self.btn_min = CaptionButton(GLYPH_WIN_MIN, "最小化")
        self.btn_max = CaptionButton(GLYPH_WIN_MAX, "最大化（铺满屏幕并隐藏任务栏）")
        self.btn_close = CaptionButton(GLYPH_WIN_CLOSE, "关闭")
        for button in (self.btn_min, self.btn_max, self.btn_close):
            button.setParent(self)

    # -- 对外 ------------------------------------------------------------

    def set_maximized(self, maximized: bool) -> None:
        """中间那颗键在"最大化 / 还原"两副面孔之间切换（与系统标题栏同规矩）。"""
        self.btn_max.set_glyph(GLYPH_WIN_RESTORE if maximized else GLYPH_WIN_MAX)
        self.btn_max.setToolTip("还原窗口" if maximized
                                else "最大化（铺满屏幕并隐藏任务栏）")

    # -- 内部 ------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        self._layout_buttons()
        super().resizeEvent(event)

    def _layout_buttons(self) -> None:
        buttons = (self.btn_close, self.btn_max, self.btn_min)     # 从右往左排
        right = self.width() - CAPTION_INSET
        top = (self.height() - self.btn_min.height()) // 2
        for button in buttons:
            right -= button.width()
            button.move(right, top)
            right -= CAPTION_GAP
        self.btn_min.raise_()
        self.btn_max.raise_()
        self.btn_close.raise_()

    def paintEvent(self, event) -> None:
        """只画左边的窗口标题（小字、次要色）。

        标题不是装饰：整条标题栏大部分是空的，光秃秃一条空白带在窗口顶部会像
        排版事故；写上窗口名既像系统标题栏，窗口化时被别的窗口压住也好认。
        左边距见 ``TITLE_LEFT``（那儿的注释解释了它为什么不是 CARD_PAD 那一套）。

        ⚠️ 这条整条**出血到窗口左右边缘**，但**不画任何材质底** —— 标题栏直接
        坐在页面背景上（页面背景是纯色，见 ``ui_backdrop``）：用户明确要过"这里
        背景透明的"，chrome 层不再跟卡片一起铺毛玻璃/液态玻璃。
        """
        if not self._title:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        left = float(self.TITLE_LEFT)

        # 先画窗口图标（系统标题栏上那枚），再接着写字
        icon = self._icon.pixmap(QSize(self.ICON_PX, self.ICON_PX),
                                 self.devicePixelRatioF())
        if not icon.isNull():
            painter.drawPixmap(QPointF(left, (self.height() - self.ICON_PX) / 2.0),
                               icon)
            left += self.ICON_PX + self.ICON_GAP

        painter.setFont(self._title_face)
        # ⚠️ 这里原本是 ink_3（**最弱**的一档，实测亮度 145）—— 窗口标题却比卡片标题
        #    （ink，243）暗一大截，用户反复反馈"字暗淡"就包含它。提到 ink_2。
        painter.setPen(qcolor("ink_2"))
        painter.drawText(QRectF(left, 0.0, float(self.width()) - left,
                                float(self.height())),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self._title)

    # -- 拖拽 ------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        window = self.window()
        if (event.button() == Qt.MouseButton.LeftButton
                and not window.isFullScreen()):
            handle = window.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        # 拖拽已经交给了系统（HTCAPTION），双击最大化由系统那条路走
        # （→ changeEvent 再转成我们的全屏），这里不再自己处理，避免双重触发
        event.accept()


class BrandHeader(QWidget):
    """品牌页头：左边整组中英文队标，右边产品名。

    不套卡片 —— 页头直接坐在背景上，比再叠一层容器轻，
    也才不会在窗口顶部堆出两层框。
    """

    def __init__(self, product: str = "串口助手", parent=None) -> None:
        super().__init__(parent)
        self._product = product
        self._product_face = ui_font(13.5)
        # 当前赛季 + 车号（用户 2026-10-22：显示在「串口调试助手」下面）。
        # 空串 = 没选车，不画第二行。
        self._vehicle_text = ""
        self._vehicle_face = ui_font(10.5)
        self._lockup: QPixmap | None = None
        self._dpr = 1.0
        self._load()
        # ⚠️ **不要**给页头设 WA_TransparentForMouseEvents。
        # 按 Qt 的定义，这个属性是"禁掉本控件**及其子控件**的鼠标事件投递"
        # （不是只作用于控件本身）—— 哪天再往页头里挂可点控件就会中招：
        # QApplication.widgetAt() 返回 None，窗口级派发的点击永远落不到子控件身上
        # （电源开关挂在页头那阵，"按钮好像失效了"就是它）。
        # 页头本来也不需要"不吃鼠标"来做穿透 —— 它上面本来就什么都没有。

    def _screen_ratio(self) -> float:
        screen = self.screen() or QGuiApplication.primaryScreen()
        return float(screen.devicePixelRatio()) if screen else 1.0

    def _load(self) -> None:
        self._dpr = self._screen_ratio()
        self._lockup, _ = lockup_pixmap(current_scheme(), LOCKUP_MIN_W, self._dpr)
        self.updateGeometry()

    @property
    def _lockup_h(self) -> float:
        """队标的逻辑高度。位图自带 devicePixelRatio，所以要除回去。"""
        if self._lockup is None:
            return 0.0
        return self._lockup.height() / max(1.0, self._lockup.devicePixelRatio())

    def _height(self) -> int:
        h = self._lockup_h
        if h <= 0.0:
            return 44
        return int(round(h + HEADER_PAD * 2))

    def sizeHint(self) -> QSize:
        return QSize(int(LOCKUP_MIN_W) + 280, self._height())

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self._height())

    def refresh(self) -> None:
        """主题或屏幕 DPI 变了就重新取/重渲染。"""
        self._load()
        self.update()

    def set_vehicle(self, text: str) -> None:
        """页头副标题：当前赛季 + 车号（空串 = 不显示）。

        只 update() 不 updateGeometry()：标题块在页头里是**右对齐居中**的，
        文字长短不改变页头高度，重绘一下就够。
        """
        value = str(text or "").strip()
        if value == self._vehicle_text:
            return
        self._vehicle_text = value
        self.update()

    def paintEvent(self, event) -> None:
        # 换显示器会换 DPI，这里顺手校一次；就是一次浮点比较，可以每帧做
        if self._screen_ratio() != self._dpr:
            self._load()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # ⚠️ 页头**不画材质底**，直接坐在页面背景上（页面背景是纯色，见
        # ``ui_backdrop``）—— 用户明确要过"这里背景透明的"：只有卡片们保留
        # 毛玻璃 / 液态玻璃，页头这层不跟。
        height = float(self.height())
        left = float(CARD_PAD + SHADOW_PAD)
        right = float(self.width()) - left

        lockup_right = left
        lockup_h = self._lockup_h
        if self._lockup is not None and lockup_h > 0.0:
            top = (height - lockup_h) / 2.0
            # 位图带 devicePixelRatio，按逻辑坐标直接画就是全物理像素分辨率
            painter.drawPixmap(QPointF(left, top), self._lockup)
            lockup_right = left + float(self._lockup.width()) / max(
                1.0, self._lockup.devicePixelRatio())

        # 产品名右对齐到最右端。窗口太窄时宁可不画，也不让它压到队标上 ——
        # 队标是品牌资产，任何情况下不能被盖住。
        # 有当前车号时画成**两行**（产品名 + 赛季/车号），整体在页头里竖直居中。
        metrics = QFontMetricsF(self._product_face)
        sub_metrics = QFontMetricsF(self._vehicle_face)
        text_w = metrics.horizontalAdvance(self._product)
        if self._vehicle_text:
            text_w = max(text_w, sub_metrics.horizontalAdvance(self._vehicle_text))
        if right - text_w > lockup_right + 24.0:
            show_sub = bool(self._vehicle_text)
            prod_h = metrics.height()
            sub_h = sub_metrics.height()
            gap = 2.0
            block_h = prod_h + (sub_h + gap if show_sub else 0.0)
            top = max(0.0, (height - block_h) / 2.0)
            painter.setFont(self._product_face)
            painter.setPen(qcolor("ink"))
            painter.drawText(QRectF(right - text_w, top, text_w, prod_h),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             self._product)
            if show_sub:
                # 副标题**居中在标题的中轴线上**（用户 2026-10-22："中轴线和
                # 串口调试助手中轴线对齐"）—— 不是各自右对齐：两行长短不同，
                # 右对齐时副标题看着往右飘。
                painter.setFont(self._vehicle_face)
                painter.setPen(qcolor("ink_3"))
                sub_w = sub_metrics.horizontalAdvance(self._vehicle_text)
                center_x = right - metrics.horizontalAdvance(self._product) / 2.0
                painter.drawText(QRectF(center_x - sub_w / 2.0,
                                        top + prod_h + gap, sub_w, sub_h),
                                 Qt.AlignmentFlag.AlignHCenter
                                 | Qt.AlignmentFlag.AlignVCenter,
                                 self._vehicle_text)

        # 页脚一条细线，把页头和内容分开 —— 不加框，只留线
        painter.setPen(QPen(qcolor("line"), 1.0))
        painter.drawLine(int(left), int(height) - 1, int(right), int(height) - 1)
