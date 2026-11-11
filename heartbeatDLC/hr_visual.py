# -*- coding: utf-8 -*-
"""心率沉浸可视化 —— 与网页「心宿 / jcjt.uno」同款效果的干净移植。

逆向来源：https://jcjt.uno/ （前端 SPA，Three.js r128 + Canvas2D，纯客户端）。
本模块是**功能性复刻（clean-room rewrite）**：仅参照其公开发布的客户端逻辑中
描述的视觉参数（粒子加色混合星点场、心跳整体缩放 30% 并泛红、红色渐变 ECG 带、
红色大字 BPM 带红光晕），用 PySide6 的 ``QPainter`` 重新实现；未复制其任何源码、
未引入 Three.js / WebGL / 其资源。配色与动效参数逐一对齐原站。

    背景       : 纯黑 (#000000)
    粒子场     : 4000 个星点（2D 近似用更少的量），体积分布球内，白偏红，加色混合；
                缓慢旋转；每次心跳整体放大 1 + pulse*0.30 并由白泛红。
    ECG 带     : 屏幕中部横向带，极淡网格 + 红色渐变线（透明→红→白头部）+ 红色辉光。
    BPM 数字   : 左上角红色大字 (#ff0033) 带红光晕，未连接/0 显示 "--"，单位 "次/分"。
    心跳节律   : 节拍按 beatInterval = 60000/bpm 自驱（与网站一致），pulse 每帧 ×0.90 衰减。

公共 API（供 hr_panel 调用）: set_bpm / set_connected / pulse / _tick / paintEvent 等。
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import (
    QDateTime, QPointF, Qt, QTimer,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
    QPainterPath, QPen, QRadialGradient,
)
from PySide6.QtWidgets import QWidget


# ---- 与网站对齐的视觉常量 ------------------------------------------------
BG = QColor(0, 0, 0)                      # --color-bg: #000000
PRIMARY = QColor(255, 255, 255)           # --color-primary: #ffffff
ACCENT = QColor(255, 0, 51)               # --color-accent: #ff0033
RED = (255, 0, 51)                        # 心跳泛红目标色

PARTICLE_COUNT = 700                      # 原站 4000（2D CPU 近似减量，保留密度观感）
SPHERE_R = 80.0                           # 星点分布球半径（与原站一致）
PULSE_DECAY = 0.90                        # 每帧衰减（与原站 animate 一致）
HEART_SCALE = 0.30                        # 心跳整体缩放量（原站 1 + pulse*0.30）
ROT_Y = 0.001                             # 每帧绕 Y 旋转（原站 starField.rotation.y）
ROT_X = 0.0005                            # 每帧绕 X 旋转（原站 starField.rotation.x）
FOCAL = 300.0                             # 透视焦距（2D 近似）
ECG_POINTS = 300                          # 原站 maxDataPoints
ECG_GRID = 40                             # 原站网格间距
BPM_GLOW = 0.5                            # 红光晕强度（对应 text-shadow 0 0 20px）

# 保留给自测 / 未来 QRS 选项（原站 ECG 用正弦扰动近似，未用真实 QRS 模板）
QRS_TEMPLATE = [                          # 28 点（占位模板，保持接口）
    0.0, 0.0, 0.0, 0.05, -0.1, 0.1, 0.0, -0.05,
    0.1, 0.35, 0.6, -0.2, 0.0, 0.05, 0.0, -0.05,
    0.0, 0.0, 0.0, 0.02, 0.0, -0.03, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0,
]


def _make_particles(n: int):
    """生成体积分布的星点（位置 + 白偏红基色），与原站 BufferGeometry 一致。"""
    pos, col = [], []
    white = (255, 255, 255)
    rnd = random.Random(20261110)          # 固定种子，保证预览/构建可复现
    for _ in range(n):
        r = SPHERE_R * (rnd.random() ** (1.0 / 3.0))   # 体积均匀
        theta = rnd.random() * 2.0 * math.pi
        phi = math.acos(2.0 * rnd.random() - 1.0)
        x = r * math.sin(phi) * math.cos(theta)
        y = r * math.sin(phi) * math.sin(theta)
        z = r * math.cos(phi)
        pos.append((x, y, z))
        # color1 白 lerp color2 红，最多 30%（原站 Math.random()*0.3）
        t = rnd.random() * 0.3
        cr = int(white[0] + (RED[0] - white[0]) * t)
        cg = int(white[1] + (RED[1] - white[1]) * t)
        cb = int(white[2] + (RED[2] - white[2]) * t)
        col.append((cr, cg, cb))
    return pos, col


class HeartVisualWidget(QWidget):
    """心宿同款心率可视化画布。每台设备一个独立实例。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 220)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        self._bpm = 0
        self._pulse = 0.0
        self._connected = False
        self._beat_interval = 0.0
        self._last_beat = 0
        self._angle_y = 0.0
        self._angle_x = 0.0

        self._pos, self._col = _make_particles(PARTICLE_COUNT)
        self._ecg = [0.5] * ECG_POINTS

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(16)        # ~60fps，与原站 rAF 一致

    # ---- 外部接口 ----------------------------------------------------------
    def set_bpm(self, bpm) -> None:
        try:
            bpm = int(bpm)
        except (TypeError, ValueError):
            bpm = 0
        self._bpm = max(0, min(255, bpm))
        self._beat_interval = (60000.0 / self._bpm) if self._bpm > 0 else 0.0

    def pulse(self, bpm=None) -> None:
        """立即触发一次心跳（与原站一致：pulseIntensity = 1.0）。"""
        if bpm is not None:
            self.set_bpm(bpm)
        if self._bpm > 0:
            self._last_beat = QDateTime.currentMSecsSinceEpoch()
            self._pulse = 1.0

    def set_connected(self, ok: bool) -> None:
        self._connected = bool(ok)
        if not ok:
            self._bpm = 0
            self._beat_interval = 0.0
            self._pulse = 0.0

    # ---- 动画节拍 ----------------------------------------------------------
    def _tick(self) -> None:
        now = QDateTime.currentMSecsSinceEpoch()
        # 按心率自驱节拍（与原站 animate 一致）
        if self._connected and self._bpm > 0 and self._beat_interval > 0:
            if now - self._last_beat >= self._beat_interval:
                self._last_beat = now
                self._pulse = 1.0
        self._pulse *= PULSE_DECAY
        if self._pulse < 0.01:
            self._pulse = 0.0
        self._angle_y += ROT_Y
        self._angle_x += ROT_X
        self._update_ecg(now)
        self.update()

    def _update_ecg(self, now) -> None:
        self._ecg.pop(0)
        if not self._connected or self._bpm == 0:
            self._ecg.append(0.5)              # 平线
            return
        if self._pulse > 0.1:
            # 原站：val = 0.5 - sin(now/20) * pulse * 0.8（视觉技巧，峰朝上）
            val = 0.5 - (math.sin(now / 20.0) * self._pulse * 0.8)
        else:
            val = 0.5 + (random.random() - 0.5) * 0.05   # 静息噪点
        self._ecg.append(val)

    # ---- 绘制 --------------------------------------------------------------
    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, ev) -> None:
        self._timer.stop()
        super().hideEvent(ev)

    def paintEvent(self, ev) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()

        # 纯黑底
        painter.fillRect(0, 0, w, h, BG)

        self._paint_particles(painter, w, h)
        self._paint_ecg(painter, w, h)
        self._paint_bpm(painter, w, h)

    def _paint_particles(self, painter, w, h) -> None:
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode_Plus)  # 加色混合
        cY = math.cos(self._angle_y)
        sY = math.sin(self._angle_y)
        cX = math.cos(self._angle_x)
        sX = math.sin(self._angle_x)
        g = 1.0 + self._pulse * HEART_SCALE
        cx = w / 2.0
        cy = h / 2.0
        SCALE = min(w, h) * 0.5 / SPHERE_R * 0.95
        p = self._pulse
        for i in range(len(self._pos)):
            x, y, z = self._pos[i]
            # 绕 Y
            x1 = x * cY - z * sY
            z1 = x * sY + z * cY
            y1 = y
            # 绕 X
            y2 = y1 * cX - z1 * sX
            z2 = y1 * sX + z1 * cX
            x2 = x1
            x2 *= g
            y2 *= g
            z2 *= g
            denom = FOCAL - z2
            if denom <= 1.0:
                continue
            f = FOCAL / denom
            sx = cx + x2 * f * SCALE
            sy = cy + y2 * f * SCALE
            depth = (f - 0.79) / (1.36 - 0.79)
            if depth < 0.0:
                depth = 0.0
            elif depth > 1.0:
                depth = 1.0
            a = 0.25 + 0.75 * depth
            br, bg, bb = self._col[i]
            # 白 → 红（原站 material.color.setHSL(0,1,1-pulse*0.5)）
            rr = int(br + (RED[0] - br) * p)
            gg = int(bg + (RED[1] - bg) * p)
            bb_ = int(bb + (RED[2] - bb) * p)
            dot_r = max(1.0, 1.7 * f * g)
            grad = QRadialGradient(sx, sy, dot_r)
            grad.setColorAt(0.0, QColor(rr, gg, bb_, int(a * 255)))
            grad.setColorAt(1.0, QColor(rr, gg, bb_, 0))
            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(sx, sy), dot_r, dot_r)
        painter.restore()

    def _paint_ecg(self, painter, w, h) -> None:
        band_h = min(180.0, h * 0.24)
        top = h / 2.0 - band_h / 2.0
        # 半透明黑底带
        painter.fillRect(0, int(top), w, int(band_h), QColor(0, 0, 0, 51))
        # 上下描边
        painter.setPen(QColor(255, 255, 255, 26))
        painter.drawLine(0, int(top), w, int(top))
        painter.drawLine(0, int(top + band_h), w, int(top + band_h))
        # 网格（极淡）
        painter.setPen(QColor(255, 255, 255, 13))
        for gx in range(0, w, ECG_GRID):
            painter.drawLine(gx, int(top), gx, int(top + band_h))
        for gy in range(int(top), int(top + band_h), ECG_GRID):
            painter.drawLine(0, gy, w, gy)

        # 折线点
        pts = []
        for i, val in enumerate(self._ecg):
            x = i * w / (ECG_POINTS - 1)
            y = top + val * band_h
            pts.append(QPointF(x, y))

        # 红色辉光（粗、半透明）
        painter.setPen(QPen(QColor(255, 0, 51, 60), 6))
        painter.drawPolyline(pts)
        # 主渐变线（透明 → 红 → 白头部）
        grad = QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0.0, QColor(255, 0, 51, 0))
        grad.setColorAt(0.8, QColor(255, 0, 51, 255))
        grad.setColorAt(1.0, QColor(255, 255, 255, 255))
        painter.setPen(QPen(grad, 2))
        painter.drawPolyline(pts)

    def _paint_bpm(self, painter, w, h) -> None:
        # 顶部警告语（与原站 h1 同样的小灰字）
        warn = "警告：心电图像为心率模拟结果，仅供娱乐，切勿代替医疗器械"
        wf = QFont("Courier New", 0)
        wf.setStyleHint(QFont.StyleHint.Monospace)
        wf.setPointSize(max(9, int(min(w, h) * 0.018)))
        painter.setFont(wf)
        painter.setPen(QColor(120, 120, 120))
        fmw = QFontMetrics(wf)
        warn = fmw.elidedText(warn, Qt.TextElideMode.ElideRight, w - 32)
        painter.drawText(16, 18, warn)

        # 数字（左上，红色大字 + 红光晕）
        text = "--" if (not self._connected or self._bpm == 0) else str(self._bpm)
        size = max(40, min(96, int(min(w, h) * 0.16)))
        font = QFont("Courier New", 0)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(size)
        font.setBold(True)
        painter.setFont(font)
        fm = QFontMetrics(font)
        base = fm.horizontalAdvance(text)
        x = 16.0
        y = 36.0 + size                      # 基线

        # 红光晕（径向，对应 text-shadow 0 0 20px）
        glow_r = base * 0.75 + size * 0.6
        halo = QRadialGradient(x + base / 2.0, y - size * 0.4, glow_r)
        halo.setColorAt(0.0, QColor(255, 0, 51, int(255 * BPM_GLOW)))
        halo.setColorAt(1.0, QColor(255, 0, 51, 0))
        painter.fillRect(int(x - glow_r), int(y - size - glow_r),
                         int(glow_r * 2 + base), int(glow_r * 2),
                         QBrush(halo))

        # 数字本体（路径填充 ACCENT，浅色需 fillPath）
        path = QPainterPath()
        path.addText(x, y, font, text)
        painter.fillPath(path, ACCENT)

        # 单位 次/分（白色小字）
        if self._bpm > 0:
            uf = QFont("Courier New", 0)
            uf.setStyleHint(QFont.StyleHint.Monospace)
            uf.setPointSize(int(size * 0.26))
            upath = QPainterPath()
            upath.addText(x + base + size * 0.18, y, uf, "次/分")
            painter.fillPath(upath, PRIMARY)
