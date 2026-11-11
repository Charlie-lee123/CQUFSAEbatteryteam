# -*- coding: utf-8 -*-
"""性能模式的提示浮层：整窗高斯模糊 + 一张自动消失的卡片。

## 为什么是"浮层"而不是 QDialog

用户点名"弹窗要高斯模糊淡入、自动消失、不需要用户确定"。``QDialog`` 会抢焦点、
挡输入、还要处理确定键 —— 这里要的是**纯装饰**：盖在接收区上看一眼，然后自己
走掉，底下的串口接收一秒都不能停。所以是一枚 ``WA_TransparentForMouseEvents``
的子控件，只画像素，不参与事件。

## 模糊怎么算

真高斯（可分离卷积、不依赖 cv2/scipy），但**先降采样再卷积**：
2900×1800 的整窗图直接卷 sigma=32 要 ~600ms，双击后那一下必卡；降 6 倍后
只需要 ~25ms，升回原尺寸时 Qt 的双线性插值会把降采样丢掉的细节补平 ——
背景本来就是一张要被糊掉的图，这个误差看不出来。

⛔ 不用 ``QGraphicsBlurEffect``：项目铁律，离屏合成会让整窗掉帧。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ui_anim import Tween, out_cubic
from ui_theme import qcolor, ui_font

# ⚠️ numpy **不在模块顶部导入**：它的冷启动成本约 80~120ms，而本模块是
#    main_window 的静态依赖 —— 放顶层等于给每次启动加一笔，只为了一个
#    用户可能一辈子不用的手势。真正要模糊时（双击之后）再 import。

#: 缩小倍数：2400×1670 的整窗图 6 倍降采样要 ~118ms、8 倍 ~70ms
#: （耗时大头是最后的平滑放大，不是卷积本身），8 倍是"够糊且不卡"的平衡点
_SHRINK = 8
#: 屏幕上的模糊半径（逻辑像素）
_SIGMA_LOGICAL = 16.0


def _gaussian(arr, sigma: float):
    """对 ``(h, w, 4)`` 的 float 数组做可分离高斯卷积（边缘复制）。"""
    if sigma <= 0.05:
        return arr
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view

    radius = max(1, int(round(sigma * 2.0)))
    xs = np.arange(-radius, radius + 1, dtype=np.float32)
    kernel = np.exp(-(xs * xs) / (2.0 * sigma * sigma))
    kernel /= kernel.sum()

    # ⛔ 窗口轴追加在**末尾**：窗口长度 L 时 shape 是 (h, w, 4, L)，
    #    所以 einsum 必须是 "hwck,k->hwc"（写成 "hwkc,k->hwc" 会抛 ValueError）。
    pad = np.pad(arr, ((0, 0), (radius, radius), (0, 0)), mode="edge")
    out = np.einsum("hwck,k->hwc",
                    sliding_window_view(pad, 2 * radius + 1, axis=1), kernel)
    pad = np.pad(out, ((radius, radius), (0, 0), (0, 0)), mode="edge")
    out = np.einsum("hwck,k->hwc",
                    sliding_window_view(pad, 2 * radius + 1, axis=0), kernel)
    return out


def _blur_pixmap(pix: QPixmap, sigma: float = _SIGMA_LOGICAL) -> QPixmap:
    """把一张位图糊掉。失败（没 numpy / 图坏了）时退回多级缩放近似。"""
    if pix is None or pix.isNull():
        return pix
    try:
        import numpy as np

        image = pix.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = image.width(), image.height()
        if width < 8 or height < 8:
            return pix
        stride = image.bytesPerLine()
        raw = np.frombuffer(image.constBits(), dtype=np.uint8,
                            count=stride * height).reshape(height, stride)
        arr = raw[:, : width * 4].reshape(height, width, 4).astype(np.float32)

        shrink = _SHRINK
        small_w, small_h = width // shrink, height // shrink
        if small_w < 8 or small_h < 8:
            shrink = 2
            small_w, small_h = width // shrink, height // shrink
        # 块平均就是理想低通：比"隔点抽样"少一大截高频噪点（抽样会让模糊图
        # 里残留一层次级波纹）。
        small = arr[: small_h * shrink, : small_w * shrink].reshape(
            small_h, shrink, small_w, shrink, 4).mean(axis=(1, 3))
        small = _gaussian(small, max(0.8, sigma / shrink))

        out = np.clip(small, 0, 255).astype(np.uint8)
        out = np.ascontiguousarray(out)
        blurred = QImage(out.data, small_w, small_h, 4 * small_w,
                         QImage.Format.Format_RGBA8888).copy()
        return QPixmap.fromImage(blurred).scaled(
            width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
    except Exception:                              # noqa: BLE001
        # 退化：反复缩小/放大（Qt 的双线性也是低通），至少不是清晰的背景
        small = pix.scaled(max(8, pix.width() // 24), max(8, pix.height() // 24),
                           Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        return small.scaled(pix.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)


class PerfModeOverlay(QWidget):
    """盖在主窗口上的一层提示。``play()`` 一次跑完整段动画。"""

    CARD_W = 382
    CARD_H = 134
    HOLD_MS = 1150

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setVisible(False)
        self._bg: QPixmap | None = None
        self._bg_alpha = 0.0
        self._card_alpha = 0.0
        self._card_scale = 0.94
        self._entering = True
        self._in: Tween | None = None
        self._out: Tween | None = None
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._fade_out)

    # -- 对外 ---------------------------------------------------------

    def play(self, entering: bool) -> None:
        """播一遍：模糊淡入 → 停留 → 一起淡出。可被下一次调用打断重播。"""
        self._entering = bool(entering)
        parent = self.parentWidget()
        if parent is None:
            return
        self._stop_anim()
        self.hide()                     # 抓图时不能把自己也抓进去
        self.setGeometry(parent.rect())
        shot = parent.grab()
        self._bg = _blur_pixmap(shot) if not shot.isNull() else None
        self._bg_alpha = 0.0
        self._card_alpha = 0.0
        self._card_scale = 0.94
        self.show()
        self.raise_()
        self.update()
        self._in = Tween(230, curve=out_cubic, on_value=self._on_fade_in,
                         on_finished=self._fade_in_done, parent=self)
        self._in.start(0.0, 1.0)

    def stop_and_close(self) -> None:
        """立刻收掉（退出窗口 / 再播一遍之前）。"""
        self._stop_anim()
        self.hide()
        self._bg = None
        self._bg_alpha = 0.0
        self._card_alpha = 0.0

    # -- 动画 ---------------------------------------------------------

    def _stop_anim(self) -> None:
        self._hold.stop()
        for tween in (self._in, self._out):
            if tween is not None and tween.running:
                tween.stop()
        self._in = None
        self._out = None

    def _on_fade_in(self, key: float) -> None:
        # 背景先到位（0~0.8 就推到接近满），卡片稍晚起、带一点"鼓出来"
        self._bg_alpha = min(1.0, key * 1.35)
        self._card_alpha = max(0.0, min(1.0, (key - 0.10) / 0.90))
        self._card_scale = 0.94 + 0.06 * key
        self.update()

    def _fade_in_done(self) -> None:
        # 窗口在淡入这 230ms 里被关掉时，宿主可能已经进了析构 ——
        # Tween 的 destroyed 回调仍会把这里叫一次，起计时器会碰已删对象。
        try:
            self._hold.start(self.HOLD_MS)
        except RuntimeError:
            pass

    def _fade_out(self) -> None:
        self._out = Tween(210, curve=out_cubic, on_value=self._on_fade_out,
                          on_finished=self.stop_and_close, parent=self)
        self._out.start(0.0, 1.0)

    def _on_fade_out(self, key: float) -> None:
        remain = max(0.0, 1.0 - key)
        self._bg_alpha = remain
        self._card_alpha = remain
        self._card_scale = 0.97 + 0.03 * key
        self.update()

    # -- 绘制 ---------------------------------------------------------

    def paintEvent(self, event) -> None:
        if self._card_alpha <= 0.001 and self._bg_alpha <= 0.001:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        if self._bg is not None and self._bg_alpha > 0.001:
            painter.setOpacity(min(1.0, self._bg_alpha) * 0.96)
            painter.drawPixmap(self.rect(), self._bg)

        if self._card_alpha <= 0.001:
            return
        painter.setOpacity(min(1.0, self._card_alpha))
        self._paint_card(painter)
        painter.end()

    def _paint_card(self, painter: QPainter) -> None:
        scale = self._card_scale
        width = self.CARD_W * scale
        height = self.CARD_H * scale
        rect = QRectF((self.width() - width) / 2.0,
                      (self.height() - height) / 2.0, width, height)

        # 阴影：多层同心圆角矩形叠出柔边。⛔ 不用 QGraphicsDropShadowEffect
        #    （离屏合成，项目铁律）。
        painter.setPen(Qt.PenStyle.NoPen)
        for step in range(14, 0, -1):
            painter.setBrush(QColor(0, 0, 0, 5))
            spread = step * 0.9
            painter.drawRoundedRect(
                rect.adjusted(-spread, -spread + 4.0, spread, spread + 4.0),
                20 + spread, 20 + spread)

        painter.setBrush(qcolor("surface"))
        painter.setPen(QPen(qcolor("line_strong"), 1.0))
        painter.drawRoundedRect(rect, 20.0, 20.0)

        left = rect.left() + 26.0
        top = rect.top() + 24.0

        # 状态点（黑白灰系统里靠"实心/空心"区分状态，不引第三种彩色）
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor("ink") if self._entering
                         else QColor(qcolor("ink_3")))
        painter.drawEllipse(QPointF(left + 4.0, top + 10.0), 4.0, 4.0)

        painter.setPen(qcolor("ink"))
        painter.setFont(ui_font(14.5, QFont.Weight.DemiBold))
        painter.drawText(QRectF(left + 18.0, top - 2.0,
                                rect.width() - 60.0, 26.0),
                         Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignVCenter, "性能模式")

        painter.setPen(qcolor("ink_3", 180))
        painter.setFont(ui_font(8.0))
        painter.drawText(QRectF(rect.right() - 170.0, top - 2.0, 144.0, 26.0),
                         Qt.AlignmentFlag.AlignRight
                         | Qt.AlignmentFlag.AlignVCenter, "PERFORMANCE")

        line_y = top + 40.0
        painter.setPen(QPen(qcolor("line_strong"), 1.0))
        painter.drawLine(QPointF(left, line_y),
                         QPointF(rect.right() - 26.0, line_y))

        painter.setPen(qcolor("ink_2"))
        painter.setFont(ui_font(11.0))
        text = ("已开启 · 数据展示切换为稳定视图" if self._entering
                else "已关闭 · 数据展示恢复原始视图")
        painter.drawText(QRectF(left, line_y + 12.0,
                                rect.width() - 52.0, 24.0),
                         Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignVCenter, text)
