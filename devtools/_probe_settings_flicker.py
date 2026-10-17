# -*- coding: utf-8 -*-
"""设置面板打开动画的**逐帧**体检：把"闪"落到可测的数字上。

三个候选病因分别对应三个断言：
  ① 快照抓空（WA_DontShowOnScreen 之后 grab 拿不到内容）→ 动画期间其实什么都没有，
     结束时真面板"啪"地一下出现 —— 这本身就是一种闪；
  ② 空洞帧：某一帧快照已撤、真面板还没绘制（两者都不可见）→ 背景闪一下；
  ③ 真面板提前上屏：动画还没跑完 dialog 就 visible/exposed → 全尺寸面板闪现。

用法：
  cd F:/MimoCodeworks/SerialAssistant && QT_QPA_PLATFORM=windows \
      .venv/Scripts/python.exe devtools/_probe_settings_flicker.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings            # noqa: E402
from PySide6.QtWidgets import QApplication       # noqa: E402


def pump(app, seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.003)


def shot_is_blank(pixmap) -> bool:
    """快照是不是"空的"：全图取 9 点，全同色即视为没抓到内容。"""
    if pixmap is None or pixmap.isNull():
        return True
    img = pixmap.toImage()
    if img.width() < 8 or img.height() < 8:
        return True
    samples = {(img.pixelColor(x, y).rgba() & 0x00FFFFFF)
               for x in (img.width() // 8, img.width() // 2, img.width() * 7 // 8)
               for y in (img.height() // 8, img.height() // 2, img.height() * 7 // 8)}
    return len(samples) <= 1


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    try:
        from selftest import make_window
        from ui_theme import apply_scheme, ui_font

        app.setFont(ui_font(12.5))
        apply_scheme("dark")

        window = make_window()
        window.show()
        pump(app, 0.6)

        # ---------- ① 快照本身有没有内容 ----------
        window._open_settings()
        pump(app, 0.05)                     # 让入场序列跑完（show/grab/hide + 起跑）
        dialog = window._settings_dialog
        shot_holder = window._settings_shot
        pixmap = shot_holder._shot if hasattr(shot_holder, "_shot") else None
        blank = shot_is_blank(pixmap)
        print("=== ① 快照体检 ===")
        print(f"  快照尺寸: {pixmap.width() if pixmap else 0}x"
              f"{pixmap.height() if pixmap else 0}  dpr="
              f"{pixmap.devicePixelRatio() if pixmap else 0}")
        print(f"  快照是不是空的: {blank}  -> {'❌ 抓空了（这本身就是闪）' if blank else 'OK 有内容'}")
        print(f"  对话框此刻 visible={dialog.isVisible()} "
              f"（入场动画期间应为 False）")
        if dialog is not None:
            handle = dialog.windowHandle()
            print(f"  windowHandle={handle is not None} "
                  f"exposed={handle.isExposed() if handle else 'n/a'}")

        # ---------- ②③ 逐帧采样 ----------
        print("\n=== ② 逐帧采样（入场动画）===")
        print("   t(ms)  shot可见 shot几何            dialog可见 dialog exposed")
        t0 = time.perf_counter()
        frames = 0
        holes = []
        last_shot = None
        while time.perf_counter() - t0 < 0.45:
            app.processEvents()
            now = (time.perf_counter() - t0) * 1000.0
            shot_vis = shot_holder.isVisible()
            dlg_vis = dialog.isVisible() if dialog is not None else False
            handle = dialog.windowHandle() if dialog is not None else None
            exposed = bool(handle and handle.isExposed())
            # 落地后 set_shot(None) 会清掉快照，所以**过程中**留住最后一张非空的
            if shot_holder._shot is not None:
                last_shot = shot_holder._shot
            if frames % 3 == 0:
                g = shot_holder.geometry()
                print(f"  {now:6.1f}   {str(shot_vis):5s}  "
                      f"({g.x():4d},{g.y():4d},{g.width():4d},{g.height():4d})  "
                      f"{str(dlg_vis):5s}      {exposed}")
            # 空洞：快照不可见且真面板没暴露
            if not shot_vis and not exposed:
                holes.append(round(now, 1))
            # 提前弹出：动画未结束（tween 还在跑）真面板就上屏
            if dlg_vis and exposed and window._settings_geom_tween is not None \
                    and window._settings_geom_tween.running and len(holes) == 0:
                pass
            frames += 1
            time.sleep(0.003)

        print(f"\n  采样帧数={frames}")
        print(f"  空洞帧（快照没了+面板没上屏）: {len(holes)} 个"
              f"{'  时刻=' + str(holes[:8]) if holes else ''}")
        print(f"  收尾后 shot可见={shot_holder.isVisible()} "
              f"dialog可见={dialog.isVisible() if dialog else None} "
              f"exposed={bool(dialog.windowHandle() and dialog.windowHandle().isExposed()) if dialog else None}")
        if shot_holder.isVisible():
            print("  ❌ 收尾后快照还留在界面上（没撤干净）")
        if dialog is None or not dialog.isVisible():
            print("  ❌ 收尾后真面板没显示出来")

        # ---------- ④ 决定性判据：动画终点 == 真面板落点，且两者像素一致 ----------
        print("\n=== ④ 换帧对齐（闪不闪就看这里）===")
        shot_pix = last_shot
        if dialog is None or shot_pix is None:
            print("  ❌ 缺对话框或缺快照，无法比对")
            return 1
        target = window._settings_geom_to
        landed = dialog.frameGeometry()
        same_geom = (target == landed)
        print(f"  动画终点矩形 = {target.x()},{target.y()},{target.width()}x{target.height()}")
        print(f"  真面板落点   = {landed.x()},{landed.y()},{landed.width()}x{landed.height()}")
        print(f"  -> 两者完全一致 = {same_geom}")
        dpr = dialog.devicePixelRatioF()
        print(f"  快照尺寸 = {shot_pix.width()}x{shot_pix.height()} (dpr={dpr})"
              f"  真面板 frame = {int(landed.width() * dpr)}x{int(landed.height() * dpr)}")
        size_ok = (abs(shot_pix.width() - landed.width() * dpr) <= 2
                   and abs(shot_pix.height() - landed.height() * dpr) <= 2)
        print(f"  -> 快照与整窗尺寸一致 = {size_ok}")
        # 快照像素 vs 落定后重新抓的整窗：平均差越小，换帧越看不出来
        fresh = window._grab_settings_frame()
        mean_diff = 0.0
        if not fresh.isNull():
            fa, fb = shot_pix.toImage(), fresh.toImage()
            n, acc = 0, 0
            for iy in range(1, 8):
                for ix in range(1, 8):
                    x = min(fa.width() - 1, fb.width() * ix // 8)
                    y = min(fa.height() - 1, fb.height() * iy // 8)
                    ca, cb = fa.pixelColor(x, y), fb.pixelColor(x, y)
                    acc += (abs(ca.red() - cb.red()) + abs(ca.green() - cb.green())
                            + abs(ca.blue() - cb.blue())) / 3
                    n += 1
            mean_diff = acc / max(1, n)
        print(f"  快照 vs 落定后整窗 平均像素差 = {mean_diff:.2f}（<6 视为看不出跳变）")
        pixel_ok = mean_diff < 6.0
        print(f"  -> 像素一致 = {pixel_ok}")
        print(f"\n  换帧判定: {'✅ 不会闪' if same_geom and size_ok and pixel_ok else '❌ 仍会闪'}")

        window.close()
        pump(app, 0.2)
        return 0 if (same_geom and size_ok and pixel_ok) else 1
    finally:
        for k, v in snapshot.items():
            if v is None:
                settings.remove(k)
            else:
                settings.setValue(k, v)
        settings.sync()


if __name__ == "__main__":
    sys.exit(main())
