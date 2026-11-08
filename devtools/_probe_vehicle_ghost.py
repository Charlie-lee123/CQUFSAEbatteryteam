# -*- coding: utf-8 -*-
r"""探针：选车号后的「旧图淡出」到底盖住了什么 —— 把覆盖层那一帧**复算成图**。

逐帧 grab 不可行（``body.grab()`` 单次 6.3ms + 首帧触发一次 700ms 级重排，
采不到 60fps 的中间帧）。但覆盖层的合成是确定的：

    显示像素 = α · 旧图(切换前同一矩形) + (1-α) · 新内容

所以只要在切换**前**抓一张、切换**后**抓同一张矩形，按真实出现过的 α 做线性
混合，就等于用户当时看到的那一帧。本探针输出四张图 + 三个量化指标：

  1. `old.png` / `new.png` / `blend_a090 / a048 / a019.png` —— 真实中间帧
  2. **未被覆盖的高度**：新布局里超出旧矩形并集的部分（这部分**没有任何过渡**，
     第一帧直接出现）
  3. **双曝光像素数**：旧图与新内容在同一像素上差 >24 灰阶的点数（= 两套数字
     叠在一起的地方）
  4. 旧矩形并集 / 新矩形并集的坐标

用法：.venv\Scripts\python.exe devtools/_probe_vehicle_ghost.py
产物：_check/vehicle_ghost/*.png + _check/vehicle_ghost.txt
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QRect, QSettings                             # noqa: E402
from PySide6.QtGui import QImage, QPainter                              # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea                  # noqa: E402

OUT_DIR = os.path.join(_ROOT, "_check", "vehicle_ghost")
OUT_TXT = os.path.join(_ROOT, "_check", "vehicle_ghost.txt")
CAR_A, CAR_B = "E74-2026", "E75-2026"
SPEC = {CAR_A: ([25, 25, 25], [8, 8, 8]),
        CAR_B: ([20] * 5, [6] * 5)}
ALPHAS = (0.90, 0.48, 0.19)          # 实测出现过的 alpha（见 vehicle_pick_frames.txt）
_lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    _lines.append(text)


def pump(app, ms: float) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


def rows_rect(dialog) -> QRect:
    rect = QRect()
    for label, holder in dialog._module_rows:
        for widget in (label, holder):
            r = widget.geometry()
            rect = QRect(r) if rect.isNull() else rect.united(r)
    return rect


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, ui_font
    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    import customization
    import ui_anim
    hz = int(customization.get("anim_fps"))
    if hz == 120:
        hz = ui_anim.display_max_hz()
    ui_anim.set_frame_hz(hz)

    from selftest import make_window, restore_settings
    settings = QSettings("MimoWorks", "SerialAssistant")
    settings.sync()
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    window = None
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        for name in list(customization.vehicle_names()):
            customization.delete_vehicle(name)
        for name, (cells, temps) in SPEC.items():
            customization.save_vehicle(name, cells, temps)
        customization.select_vehicle(CAR_A)

        window = make_window()
        window.setGeometry(60, 40, 1134, 820)
        window.show()
        pump(app, 700)
        window._open_settings()
        pump(app, 900)
        dialog = window._settings_dialog
        if dialog is None:
            say("⛔ 设置面板没打开")
            return 3
        from PySide6.QtWidgets import QLabel
        scroll = dialog.findChild(QScrollArea)
        caption = next((label for label in dialog.findChildren(QLabel)
                        if label.text().startswith("车辆（电池箱）")), None)
        if caption is not None:
            scroll.ensureWidgetVisible(caption, 0, 8)
        pump(app, 400)

        body = dialog.form.parentWidget()
        say(f"=== 环境 ===  {hz}Hz  面板 {dialog.frameGeometry().width()}x"
            f"{dialog.frameGeometry().height()}  body {body.width()}x{body.height()}")
        say(f"  滚动 value={scroll.verticalScrollBar().value()} "
            f"max={scroll.verticalScrollBar().maximum()}")

        # ---------- 3 → 5 ----------
        old_rect = rows_rect(dialog)
        pad = 14
        grab_rect = old_rect.adjusted(-pad, -pad, pad, pad) & body.rect()
        old_pix = body.grab(grab_rect)
        say(f"\n[3模组→5模组]  旧行并集 = {old_rect}  抓图矩形 = {grab_rect}")
        dialog.cmb_vehicle_number.setCurrentIndex(
            dialog.cmb_vehicle_number.findData(CAR_B))
        pump(app, 40)                      # 覆盖层已贴上、还在最不透明的阶段
        overlay = getattr(dialog, "_rows_fade", None)
        alpha_now = overlay._alpha if overlay is not None else 0.0
        say(f"  切换后 40ms：覆盖层 alpha={alpha_now:.2f}  几何={overlay.geometry() if overlay else None}")
        # ⚠ body.grab() 会把**子控件（覆盖层）**一起抓进去，抓到的就不是"纯新内容"
        #   了。这里临时把覆盖层藏起来抓一张"新内容本相"，再放回去继续淡出。
        if overlay is not None:
            overlay.hide()
        new_pix = body.grab(grab_rect)
        if overlay is not None:
            overlay.show()
        new_rect = rows_rect(dialog)
        say(f"  新行并集 = {new_rect}")
        uncovered = new_rect.height() - old_rect.height()
        say(f"  ⚠ 超出旧矩形并集的高度 = {uncovered}px（{body.width()}px 宽 → "
            f"约 {uncovered * body.width() / 1000:.0f}k 像素**没有任何过渡**，第一帧直接出现）")

        # 双曝光：同一像素上旧/新差多少
        old_img = old_pix.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
        new_img = new_pix.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
        w, h = old_img.width(), old_img.height()
        old_bytes = bytes(old_img.constBits())
        new_bytes = bytes(new_img.constBits())
        diff_points = sum(1 for a, b in zip(old_bytes, new_bytes) if abs(a - b) > 24)
        say(f"  旧图与新内容在同一矩形内差>24 灰阶的像素 = {diff_points}"
            f"（{diff_points / (w * h) * 100:.1f}% 的面积）→ 这些点在第一帧是"
            f"**两套内容叠在一起**")

        for alpha in ALPHAS:
            blended = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
            blended.fill(0)
            painter = QPainter(blended)
            painter.drawImage(0, 0, new_img.convertToFormat(
                QImage.Format.Format_ARGB32_Premultiplied))
            painter.setOpacity(alpha)
            painter.drawImage(0, 0, old_img.convertToFormat(
                QImage.Format.Format_ARGB32_Premultiplied))
            painter.end()
            path = os.path.join(OUT_DIR, f"blend_a{int(alpha * 100):03d}.png")
            blended.save(path)
            say(f"  已存 {path}")
        old_pix.save(os.path.join(OUT_DIR, "old.png"))
        new_pix.save(os.path.join(OUT_DIR, "new.png"))
        say(f"  已存 old.png / new.png（{w}x{h}）")

        # ---------- 5 → 3（反方向）----------
        pump(app, 1200)
        old_rect = rows_rect(dialog)
        grab_rect = old_rect.adjusted(-pad, -pad, pad, pad) & body.rect()
        dialog.cmb_vehicle_number.setCurrentIndex(
            dialog.cmb_vehicle_number.findData(CAR_A))
        pump(app, 40)
        overlay = getattr(dialog, "_rows_fade", None)
        say(f"\n[5模组→3模组]  旧行并集 = {old_rect}  覆盖层几何 = "
            f"{overlay.geometry() if overlay else None}  alpha="
            f"{overlay._alpha if overlay else 0:.2f}")
        new_rect = rows_rect(dialog)
        say(f"  新行并集 = {new_rect}  覆盖层比新内容高 "
            f"{old_rect.height() - new_rect.height()}px → 这一截旧图淡出时盖在"
            f"**已经上移的其它控件**上")
        pix = body.grab(old_rect.adjusted(-pad, -pad, pad, pad) & body.rect())
        pix.save(os.path.join(OUT_DIR, "shrink_overlay_area_new.png"))
        say(f"  已存 shrink_overlay_area_new.png")
        return 0
    finally:
        if window is not None:
            try:
                if window._settings_dialog is not None:
                    window._close_settings_dialog(accepted=False)
                window.close()
            except RuntimeError:
                pass
        pump(app, 300)
        restore_settings(settings, snapshot)
        settings.sync()
        with open(OUT_TXT, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_lines))
        print(f"产物：{OUT_TXT}")


if __name__ == "__main__":
    sys.exit(main())
