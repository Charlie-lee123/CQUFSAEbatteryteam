"""打开设置动画「最后一帧闪」真·屏幕逐帧抓拍。

复用 _qa_anim_verdict 的 _boot_single 起手；打开设置后用 QScreen.grabWindow
对**真实合成后的屏幕**逐帧抓拍，重点量「标题栏区域亮度」在换帧那一下的变化。

假设（来自代码走查）：
  - _open_settings: dialog 在屏幕外 show()+activateWindow() 后抓取快照
    （标题栏=激活态·亮），见 main_window.py:4671-4684。
  - _on_settings_geometry_done opening 分支: dialog.show()+raise_()，**没有**
    activateWindow()（main_window.py:4913-4915）。若 show() 没能真正激活窗口，
    真面板标题栏以「非激活态·暗」上屏；而垫在上头的快照（亮）正 96ms 渐隐，
    末刻亮标题栏骤暗一下 = 用户说的「打开时最后一帧闪」。

本探针只认真实像素：
  · 逐帧抓「当前可见标题栏条」所在屏幕矩形（动画期跟 _settings_shot 几何，
    落定后跟 dialog.frameGeometry），算该条均值亮度（0~255）。
  · 同时记 dialog.isActiveWindow()（若平台支持）。
  · 抓拍前/中/后若干帧存 PNG 供肉眼复核。
  · 输出 CSV：t_ms, phase, active, tb_lum, body_lum, frame_delta。

判据：若「落定后真实标题栏亮度」明显低于「换帧前快照标题栏亮度」（Δ>12），
      则激活态不一致假设成立，末帧闪根因即此。
"""
import os
import sys
import time
import csv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location(
    "qa", os.path.join(_ROOT, "devtools", "_qa_anim_verdict.py"))
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)

FRAMES_DIR = os.path.join(_ROOT, "devtools", "_frames_open")
os.makedirs(FRAMES_DIR, exist_ok=True)
CSV_PATH = os.path.join(FRAMES_DIR, "timeline.csv")


def _mean_lum(img: QImage, stride: int = 2) -> float:
    """整张图（应为标题条小图）的均值亮度（0.3R+0.59G+0.11B）。"""
    if img.isNull() or img.width() < 1 or img.height() < 1:
        return float("nan")
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    total = 0
    n = 0
    for y in range(0, h, stride):
        for x in range(0, w, stride):
            c = img.pixelColor(x, y)
            total += 0.3 * c.red() + 0.59 * c.green() + 0.11 * c.blue()
            n += 1
    return (total / n) if n else float("nan")


def _grab_strip(screen, rect: QRect):
    """抓屏幕矩形（逻辑坐标），返回 QImage。"""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    if w < 2 or h < 2:
        return QImage()
    # grabWindow 接受逻辑坐标；高 DPR 下返回设备分辨率 pixmap
    pix = screen.grabWindow(0, x, y, w, h)
    return pix.toImage().convertToFormat(QImage.Format_RGB888)


def _current_title_rect(window) -> QRect | None:
    """当前可见标题栏所在的屏幕矩形（逻辑坐标）。"""
    try:
        shot = window._settings_shot
        if shot is not None and shot.isVisible():
            g = shot.geometry()
            return QRect(g.x(), g.y(), g.width(), min(28, g.height()))
        dlg = window._settings_dialog
        if dlg is not None:
            f = dlg.frameGeometry()
            return QRect(f.x(), f.y(), f.width(), 28)
    except RuntimeError:
        return None
    return None


def _current_body_rect(window) -> QRect | None:
    try:
        dlg = window._settings_dialog
        if dlg is not None:
            f = dlg.frameGeometry()
            cy = f.y() + int(f.height() * 0.45)
            return QRect(f.x() + 40, cy, max(40, f.width() - 80), 40)
        shot = window._settings_shot
        if shot is not None and shot.isVisible():
            g = shot.geometry()
            cy = g.y() + int(g.height() * 0.45)
            return QRect(g.x() + 20, cy, max(20, g.width() - 40), 30)
    except RuntimeError:
        return None
    return None


def main() -> int:
    app, window, ctx = qa._boot_single()
    screen = QApplication.primaryScreen()
    if screen is None:
        print("FATAL: 无主屏幕（headless？无法真屏抓拍）")
        qa._teardown_single(app, window, ctx)
        return 2

    print(f"screen={screen.name()} size={screen.size().width()}x"
          f"{screen.size().height()} dpr={screen.devicePixelRatio()}")

    # 基准：主窗口标题栏亮度（对照组，应恒定）
    main_tb = QRect(window.frameGeometry().x(), window.frameGeometry().y(),
                    window.frameGeometry().width(), 28)
    base_lum = _mean_lum(_grab_strip(screen, main_tb))
    print(f"main title-bar baseline lum = {base_lum:.1f}")

    rows = []
    t0 = time.perf_counter()

    # 打开前存一帧（主窗区域）
    pre = screen.grabWindow(0, window.frameGeometry().x(),
                            window.frameGeometry().y(),
                            window.frameGeometry().width(),
                            window.frameGeometry().height())
    pre.save(os.path.join(FRAMES_DIR, "frame_pre.png"))
    rows.append(((time.perf_counter() - t0) * 1000.0, "pre", False,
                 float("nan"), float("nan"), 0.0))

    # 触发打开（同步跑到启动补间）
    window.btn_settings.click()
    target = getattr(window, "_settings_geom_to", None)
    if target is not None:
        print(f"target frame = ({target.x()},{target.y()}) "
              f"{target.width()}x{target.height()}")

    last_tb = None
    last_body = None
    saved_bodies = 0
    settle_start = None

    def record(phase: str):
        nonlocal last_tb, last_body, saved_bodies
        tb_rect = _current_title_rect(window)
        body_rect = _current_body_rect(window)
        tb_lum = _mean_lum(_grab_strip(screen, tb_rect)) if tb_rect else float("nan")
        body_lum = (_mean_lum(_grab_strip(screen, body_rect))
                    if body_rect else float("nan"))
        dlg_active = False
        try:
            dlg_active = bool(window._settings_dialog.isActiveWindow())
        except (RuntimeError, AttributeError):
            dlg_active = False
        delta = (abs(tb_lum - last_tb) if (last_tb is not None
                and not (isinstance(tb_lum, float) and tb_lum != tb_lum))
                else 0.0)
        rows.append(((time.perf_counter() - t0) * 1000.0, phase, dlg_active,
                     tb_lum, body_lum, delta))
        last_tb, last_body = tb_lum, body_lum
        # 落定后存几张面板帧供肉眼看
        if phase in ("late", "settled") and saved_bodies < 4 and body_rect:
            f = screen.grabWindow(0, body_rect.x(), body_rect.y(),
                                  body_rect.width(), body_rect.height())
            f.save(os.path.join(FRAMES_DIR, f"frame_{phase}_{saved_bodies}.png"))
            saved_bodies += 1

    # 主捕获循环：覆盖动画(260ms)+换帧渐隐(96ms)+余量
    deadline = t0 + 1.2
    while time.perf_counter() < deadline:
        app.processEvents()
        now = (time.perf_counter() - t0) * 1000.0
        # 判定阶段
        swapping = getattr(window, "_settings_swap_target", None) is not None
        shot_visible = False
        try:
            shot_visible = window._settings_shot.isVisible()
        except RuntimeError:
            pass
        if swapping or shot_visible:
            phase = "anim" if not swapping else "swap"
        else:
            # 换帧结束（snapshot 撤走、swap_target 清空）= 已落定
            if settle_start is None and now > 350:  # 过完动画段
                settle_start = now
                phase = "settled"
            else:
                phase = "late" if now > 250 else "anim"
        if now > 250 and now < 270:
            phase = "late"
        record(phase)
        # 抓拍末帧后再跑 250ms 收尾就停
        if settle_start is not None and (now - settle_start) > 250:
            break
        time.sleep(0.006)

    # 落定后补一帧（真面板）
    try:
        dlg = window._settings_dialog
        if dlg is not None:
            f = dlg.frameGeometry()
            full = screen.grabWindow(0, f.x(), f.y(), f.width(), f.height())
            full.save(os.path.join(FRAMES_DIR, "frame_final_panel.png"))
            print(f"final panel active={dlg.isActiveWindow()} "
                  f"frame=({f.x()},{f.y()}){f.width()}x{f.height()}")
    except RuntimeError:
        pass

    # 写 CSV
    with open(CSV_PATH, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t_ms", "phase", "dialog_active", "titlebar_lum",
                    "body_lum", "tb_frame_delta"])
        for r in rows:
            w.writerow([f"{r[0]:.1f}", r[1], int(r[2]),
                        (f"{r[3]:.2f}" if r[3] == r[3] else "nan"),
                        (f"{r[4]:.2f}" if r[4] == r[4] else "nan"),
                        f"{r[5]:.2f}"])

    # 分析
    anim_lums = [r[3] for r in rows if r[1] in ("anim", "late") and r[3] == r[3]]
    settled_lums = [r[3] for r in rows if r[1] == "settled" and r[3] == r[3]]
    snap_ref = max(anim_lums) if anim_lums else float("nan")
    settled_ref = (sum(settled_lums) / len(settled_lums)
                   if settled_lums else float("nan"))
    max_delta = max((r[5] for r in rows if r[5] == r[5]), default=0.0)
    print("\n=== 分析 ===")
    print(f"换帧前快照标题栏亮度参考(动画期峰值) = {snap_ref:.1f}")
    print(f"落定后真实标题栏亮度均值           = {settled_ref:.1f}")
    if snap_ref == snap_ref and settled_ref == settled_ref:
        print(f"Δ(快照-真实) = {snap_ref - settled_ref:+.1f}")
        if (snap_ref - settled_ref) > 12:
            print(">>> 激活态不一致假设【成立】：真面板标题栏比快照暗 "
                  "→ 末帧骤暗=用户所见闪。")
        else:
            print(">>> 标题栏亮度一致，末帧闪另有根因。")
    print(f"逐帧标题栏最大单帧跳变 = {max_delta:.1f}")
    print(f"CSV: {CSV_PATH}")
    print(f"frames: {FRAMES_DIR}")

    qa._teardown_single(app, window, ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
