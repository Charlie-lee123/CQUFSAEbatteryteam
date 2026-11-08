# -*- coding: utf-8 -*-
r"""复现「数据位」下拉在**高频点击开关**时框上边残影闪烁。

背景：ui_controls.py 的 ChevronComboBox 弹层是原生顶层窗口
（Popup | FramelessWindowHint | WA_TranslucentBackground），并设了
DWMWCP_ROUND（圆角阴影）。展开动画 150ms，从 25% 起始高度「长」到满高。

用户报：串口设置里「数据位」那个下拉，高频点击开关时，**框的上边有残影闪烁**
（应向下展开，顶部边缘闪残影）。

本探针两类证据都要拿：
  ① 几何根因（show/hide 竞态）：逐帧记录容器 geometry()，看有没有任何一帧
     出现在「start_h 小条 → _popup_target 满高」预期生长路径之外 —— 尤其
     「先以满高/旧位置可见一帧，再缩回 start_h」。
  ② 绘制根因（DWM 透明窗残影）：在开关瞬间抓屏（桌面区域），目视框上边有没有
     残留旧帧的列表内容（截图比几何更能暴露 DWM backing-store 残留）。

做法：用最小复刻（一个 ChevronComboBox，配置同 main_window.cmb_bytesize）
      跑 QT_QPA_PLATFORM=windows；~120ms 高频 toggle 40 次；循环里 ~2ms 轮询
      容器 geometry；并在每个开关瞬间抓屏若干帧。

用法：python devtools/_probe_popup_flicker.py
产物：
  _check/popup_flicker_geom.txt   逐帧几何原始数据 + 异常帧判定
  devtools/_frames_flicker/*.png  开关瞬间的桌面截图（目视残影用）
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import QElapsedTimer, QPoint, QRect, QTimer, Qt  # noqa: E402
from PySide6.QtGui import QScreen                                       # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox,             # noqa: E402
                               QMainWindow, QWidget, QVBoxLayout)

import ui_controls
from ui_controls import ChevronComboBox

GEOM_OUT = os.path.join(_ROOT, "_check", "popup_flicker_geom.txt")
FRAME_DIR = os.path.join(_ROOT, "devtools", "_frames_flicker")
TOGGLE_MS = 120          # 高频开关周期
RUN_MS = 40 * TOGGLE_MS  # 跑 40 个开关 ≈ 4.8s
POLL_SLEEP = 0.002       # 轮询步长 ≈ 2ms


def _snap_settings() -> dict:
    from PySide6.QtCore import QSettings
    s = QSettings()
    return {k: s.value(k) for k in s.allKeys()}


def _restore_settings(snap: dict) -> None:
    from PySide6.QtCore import QSettings
    s = QSettings()
    cur = set(s.allKeys())
    for k in cur - set(snap.keys()):
        s.remove(k)
    for k, v in snap.items():
        s.setValue(k, v)
    s.sync()


def _build_combo() -> ChevronComboBox:
    """配置同 main_window.cmb_bytesize：非编辑、AdjustToContents、5/6/7/8。"""
    c = ChevronComboBox()
    c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    for n in ("5", "6", "7", "8"):
        c.addItem(n)
    c.setCurrentText("8")
    return c


class Harness:
    def __init__(self) -> None:
        self.app = QApplication(sys.argv)
        self.app.setStyle("Fusion")
        from ui_theme import apply_scheme, detect_system_scheme, ui_font
        apply_scheme(detect_system_scheme())
        self.app.setFont(ui_font(12.5))

        self.win = QMainWindow()
        self.win.setWindowTitle("popup flicker probe")
        central = QWidget()
        self.win.setCentralWidget(central)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(40, 200, 40, 40)   # 把下拉放在屏幕中上部，下方有展开空间
        self.combo = _build_combo()
        lay.addWidget(self.combo)
        self.win.resize(900, 700)
        self.win.show()
        # 让窗口真正上屏、定位完成
        for _ in range(60):
            self.app.processEvents()
            time.sleep(0.003)

    # —— 容器访问 ——
    def _container(self):
        try:
            v = self.combo.view()
            if v is None:
                return None
            w = v.window()
            return w
        except RuntimeError:
            return None

    def container_visible(self) -> bool:
        c = self._container()
        return bool(c and c.isVisible())

    def container_geom(self) -> QRect:
        c = self._container()
        if c is None:
            return QRect()
        return c.geometry()

    def toggle(self) -> None:
        if self.container_visible():
            self.combo.hidePopup()
        else:
            self.combo.showPopup()

    def grab_region(self, name: str) -> None:
        """抓桌面区域（含弹层，因为是独立顶层窗）—— 目视残影用。"""
        c = self._container()
        if c is None or not c.isVisible():
            return
        g = c.geometry()
        # 扩一点，把下拉框本体 + 弹层 + 上方一点都框进来（看上边残影）
        x = max(0, g.x() - 10)
        y = max(0, g.y() - 40)
        w = g.width() + 20
        h = g.height() + 60
        screen: QScreen = self.app.primaryScreen()
        pix = screen.grabWindow(0, x, y, w, h)
        if not os.path.isdir(FRAME_DIR):
            os.makedirs(FRAME_DIR, exist_ok=True)
        pix.save(os.path.join(FRAME_DIR, f"{name}.png"))


def analyse_geom(rows: list[list]) -> list[str]:
    """rows: [t_ms, visible, x, y, w, h]"""
    out: list[str] = []
    # 先找稳定的满高（第一次稳定展开后量到的高度）
    full_h = 0
    for r in rows:
        if r[1] and r[5] > full_h:
            full_h = r[5]
    if full_h == 0:
        out.append("⚠️ 没有量到任何可见帧 —— 弹层可能根本没开起来")
        return out
    start_h = max(14, int(full_h * 0.25))
    out.append(f"满高 H_full={full_h}px，起始条 start_h≈{start_h}px")
    threshold = max(start_h + 8, int(full_h * 0.6))   # 超过这条线算「接近满高」

    # 切出每个展开周期（visible 由 False→True 为起点，True→False 为终点）
    cycles: list[list] = []
    cur: list = []
    prev_vis = False
    for r in rows:
        vis = bool(r[1])
        if vis and not prev_vis:
            if cur:
                cycles.append(cur)
            cur = [r]
        elif vis and prev_vis:
            cur.append(r)
        elif not vis and prev_vis:
            cur.append(r)
            cycles.append(cur)
            cur = []
        prev_vis = vis
    if cur:
        cycles.append(cur)

    anomaly_full_first = 0
    for i, cyc in enumerate(cycles):
        if not cyc:
            continue
        vis_rows = [r for r in cyc if r[1]]
        if not vis_rows:
            continue
        first = vis_rows[0]
        first_h = first[5]
        # 根因①签名：展开周期的第一帧可见高度就接近满高（在动画还没长上去之前）
        if first_h >= threshold:
            # 还要确认后面确实缩回了 start_h（即真的是「满高闪一下再缩」）
            later_small = any(r[5] <= start_h + 4 for r in vis_rows[1:8])
            if later_small:
                anomaly_full_first += 1
                out.append(f"  周期#{i}: ❌ 第一可见帧 h={first_h}（≈满高）"
                           f" 随后缩回，t={first[0]:.0f}ms —— 符合根因①（满高先可见一帧）")
    out.append(f"\n根因①（满高先可见一帧）命中周期数：{anomaly_full_first} / {len(cycles)}")
    if anomaly_full_first == 0:
        out.append("→ 几何层面**未**出现「满高先可见再缩回」；若仍有残影则是绘制根因②（DWM）")
    return out


def main() -> int:
    snap = _snap_settings()
    try:
        h = Harness()
        rows: list[list] = []
        t0 = QElapsedTimer()
        t0.start()
        next_toggle = 0
        frame_idx = 0
        last_full_grab_t = -1e9
        while t0.elapsed() < RUN_MS:
            el = t0.elapsed()
            if el >= next_toggle:
                was_visible = h.container_visible()
                h.toggle()
                # 开关瞬间抓屏：开的那一下抓，关的不抓
                if not was_visible and h.container_visible():
                    for j in range(5):
                        h.grab_region(f"t{frame_idx:03d}_open_{j}")
                        h.app.processEvents()
                        time.sleep(0.008)
                    frame_idx += 1
                next_toggle += TOGGLE_MS
            c = h._container()
            vis = bool(c and c.isVisible())
            g = c.geometry() if c is not None else QRect()
            rows.append([round(el, 1), 1 if vis else 0, g.x(), g.y(),
                         g.width(), g.height()])
            # 跑动画的其余时间也零星抓几张（看展开中残留）
            if vis and el - last_full_grab_t > 24 and frame_idx < 24:
                h.grab_region(f"t{frame_idx:03d}_mid")
                last_full_grab_t = el
                frame_idx += 1
            h.app.processEvents()
            time.sleep(POLL_SLEEP)

        # 收尾：几何搬回 + 关窗（探针不碰用户配置）
        h.win.setGeometry(0, 0, 900, 700)
        h.win.close()
        h.app.processEvents()

        report = analyse_geom(rows)
        raw = ["t_ms\tvis\tx\ty\tw\th"]
        for r in rows:
            raw.append("\t".join(str(v) for v in r))
        text = "\n".join(raw) + "\n\n" + "\n".join(report)
        print(text)
        os.makedirs(os.path.dirname(GEOM_OUT), exist_ok=True)
        with open(GEOM_OUT, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"\n逐帧几何 → {GEOM_OUT}")
        print(f"截图目录 → {FRAME_DIR}")
        return 0
    finally:
        _restore_settings(snap)


if __name__ == "__main__":
    raise SystemExit(main())
