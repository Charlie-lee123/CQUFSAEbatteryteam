# -*- coding: utf-8 -*-
"""把"打开设置时闪一下"落到**屏幕像素**上：逐帧抓屏，看是哪一帧、哪一块在变。

为什么必须抓屏而不是抓窗口：动画期间画面上有 **三个**合成来源 —— 主窗口、
快照容器（`_SettingsShot`，带 Qt.ToolTip 标志 = 独立顶层窗口）、真面板（顶层对话框）。
`QWidget.grab()` / PrintWindow 只拿得到单个窗口自己的内容，看不到叠加结果；
只有抓屏才能看见用户眼睛真正看到的那一层。

判读：
  · 亮度突变（某帧整体亮/暗很多）      → 闪
  · 某帧几乎是纯背景色                 → 空洞
  · 落地帧与前一帧内容差异大但尺寸一致  → 快照→真面板的"啪"一下（合成切换）

用法：cd F:/MimoCodeworks/SerialAssistant && QT_QPA_PLATFORM=windows \
      .venv/Scripts/python.exe devtools/_probe_settings_screen.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings, QPoint                  # noqa: E402
from PySide6.QtWidgets import QApplication                    # noqa: E402

OUT = os.path.join(_ROOT, "_check", "settings_frames")


def luma(px):
    return 0.299 * px.red() + 0.587 * px.green() + 0.114 * px.blue()


def grab_region(screen, x, y, w, h):
    """抓屏一块，返回 (平均亮度, 9 点采样列表, QImage)。"""
    pm = screen.grabWindow(0, x, y, w, h)
    img = pm.toImage()
    if img.isNull():
        return 0.0, [], None
    pts = [img.pixelColor(img.width() * i // 8, img.height() * j // 8)
           for i in range(1, 8) for j in range(1, 8)]
    mean = sum(luma(p) for p in pts) / len(pts)
    return mean, [(p.red(), p.green(), p.blue()) for p in pts], img


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    os.makedirs(OUT, exist_ok=True)
    try:
        from selftest import make_window
        from ui_theme import apply_scheme, ui_font

        app.setFont(ui_font(12.5))
        apply_scheme("dark")

        window = make_window()
        # 固定位置/大小，抓屏区域才可复现（别用全屏，全屏的 DWM 路径不一样）
        window.setGeometry(220, 140, 1134, 820)
        window.show()
        window.raise_()
        window.activateWindow()
        for _ in range(60):
            app.processEvents()
            time.sleep(0.005)

        screen = app.primaryScreen()
        origin = window.mapToGlobal(QPoint(0, 0))
        gx, gy = origin.x(), origin.y()
        # 抓窗口中间那块：设置面板最终就落在这一带
        rx, ry = gx + 260, gy + 60
        rw, rh = 640, 620
        print(f"抓屏区域 = ({rx},{ry}) {rw}x{rh}")

        base_mean, _, _ = grab_region(screen, rx, ry, rw, rh)
        print(f"打开前平均亮度 = {base_mean:.1f}")

        t0 = time.perf_counter()
        window._open_settings()
        frames = []
        prev_pts = None
        idx = 0
        while time.perf_counter() - t0 < 0.55:
            app.processEvents()
            mean, pts, img = grab_region(screen, rx, ry, rw, rh)
            now = (time.perf_counter() - t0) * 1000.0
            diff = 0.0
            if prev_pts:
                diff = sum(abs(a[i] - b[i])
                           for a, b in zip(pts, prev_pts)
                           for i in range(3)) / (len(pts) * 3)
            uniq = len(set(pts))
            frames.append((now, mean, diff, uniq))
            if img is not None and idx % 2 == 0:
                img.save(os.path.join(OUT, f"f{idx:02d}_{now:06.1f}ms.png"))
            prev_pts = pts
            idx += 1
            time.sleep(0.002)

        print(f"\n  帧数={len(frames)}  （已存图 → {OUT}）")
        print("   t(ms)   平均亮度  帧间差  采样色数")
        prev_mean = base_mean
        for now, mean, diff, uniq in frames:
            flag = ""
            dm = mean - prev_mean
            if abs(dm) > 12:
                flag += " ←亮度突跳"
            if uniq <= 2:
                flag += " ←近乎纯色(空洞?)"
            if diff > 10:
                flag += " ←帧间大改"
            print(f"  {now:6.1f}  {mean:7.1f}  {diff:6.1f}   {uniq:3d}{flag}")
            prev_mean = mean

        print("\n=== 判定 ===")
        jumps = [(n, m) for n, m, d, u in frames if abs(m - base_mean) > 20]
        holes = [n for n, m, d, u in frames if u <= 2]
        big = [(n, d) for n, m, d, u in frames if d > 10]
        print(f"  亮度偏离基线 >20 的帧: {len(jumps)} 个")
        print(f"  近乎纯色(空洞)的帧  : {len(holes)} 个 {holes[:6]}")
        print(f"  帧间平均差 >10 的帧  : {len(big)} 个 {[(round(n), round(d, 1)) for n, d in big[:8]]}")

        # 收尾
        for _ in range(40):
            app.processEvents()
            time.sleep(0.004)
        window.close()
        for _ in range(20):
            app.processEvents()
            time.sleep(0.004)
        return 0
    finally:
        for k, v in snapshot.items():
            if v is None:
                settings.remove(k)
            else:
                settings.setValue(k, v)
        settings.sync()


if __name__ == "__main__":
    sys.exit(main())
