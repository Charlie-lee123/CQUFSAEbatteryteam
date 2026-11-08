# -*- coding: utf-8 -*-
r"""开屏落点对齐探针：量「注入落点」与「主窗页头 ink_global_rect」的逐像素偏差。

目的（od51 改前/改后对照）
--------------------------
开屏落点由 ``SplashScreen.set_land_from_window`` 在**主窗 reveal 之后同一调用栈内**
注入（main._build_window → _reveal_main(sync=True) → set_land_from_window）。
若那一刻 Qt 布局/窗口几何体尚未最终化，``mapToGlobal`` 会拿到陈旧几何 → 落点偏位
（病灶 A）。本探针精确复刻该 t=0 时序：show + apply_startup_state + repaint 后
**立即**调 set_land_from_window（不提前 settle），再把窗口真正 settle，比对：

    dx = 注入时 ink 左上角全局 x  −  现已 settle 的 ink_global_rect().x()
    dy = 同上 y
    dw = 注入时 ink 宽            −  ink_global_rect().width()
    dh = 注入时 ink 高            −  ink_global_rect().height()

⛔ 只读、不污染 QSettings：开窗前对 QSettings 全量快照，finally 原样还原
（新键删除、关闭实例后再还原，避免析构回写）。

⛔ 不录屏、不改产品源码、不 git、不打包、不动 发布/ 与 dist*。
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPointF, QSettings
from PySide6.QtWidgets import QApplication

_APP, _ORG = "SerialAssistant", "MimoWorks"


def main() -> int:
    st = QSettings(_ORG, _APP)
    SNAP = {k: st.value(k) for k in st.allKeys()}
    try:
        # 强制走原生几何（planB 含落点段），保证 _motion 被创建
        st.setValue("custom/splash_anim", "plan:planb")
        st.sync()

        app = QApplication(sys.argv)

        import main_window
        import splash_anim

        # ---- 复刻 main._reveal_main(sync=True) 的 t=0 时序（不提前 settle）----
        win = main_window.MainWindow()
        win._skip_startup_anim = True
        win.show()
        win.apply_startup_state()
        win.repaint()                       # 同步画首帧（与真实启动一致）

        # ---- 起开屏（创建 _motion、show+repaint），随即停掉所有定时器 ----
        splash = splash_anim.play_configured()
        if splash is None or splash._motion is None:
            print("ERROR: 未能创建几何开屏（_motion 为 None），无法量落点")
            return 2
        for _t in (splash._motion_timer, splash._max_timer, splash._fade_timer):
            if _t is not None:
                _t.stop()
        # 复刻 main._build_window 里同一调用栈的落点注入（t=0，未 settle）
        try:
            splash.set_land_from_window(win)
        except RuntimeError as exc:
            print(f"set_land_from_window 抛 RuntimeError：{exc!r}")
            return 2

        land = splash._motion._land
        sw = float(splash.width())
        sh = float(splash.height())
        spec = splash._motion._spec
        print(f"splash 尺寸 = {sw:.1f}x{sh:.1f}  dpr={splash.devicePixelRatioF()}")
        print(f"注入 _land（归一化）= x={land[0]:.5f} y={land[1]:.5f} "
              f"h={land[2]:.5f}")

        # ---- 真正 settle：让布局/窗口管理器几何最终化 ----
        for _ in range(30):
            app.processEvents()
        time.sleep(0.15)
        for _ in range(10):
            app.processEvents()

        g_local = win.brand_header.ink_rect()
        g_top = win.brand_header.mapToGlobal(QPointF(g_local.x(), g_local.y()))
        gx0, gy0 = float(g_top.x()), float(g_top.y())
        gw0, gh0 = float(g_local.width()), float(g_local.height())
        # 由 _land 反算「注入时」的全局墨迹盒（splash 几何未变 → mapToGlobal 可逆）
        inj_pt = splash.mapToGlobal(QPointF(land[0] * sw, land[1] * sh))
        inj_x = float(inj_pt.x())
        inj_y = float(inj_pt.y())
        inj_h = land[2] * sh
        inj_w = inj_h * spec.lockup_aspect     # ink 盒与 lockup 同宽高比

        dx = inj_x - gx0
        dy = inj_y - gy0
        dw = inj_w - gw0
        dh = inj_h - gh0

        print(f"\n已 settle 的 ink_global_rect = "
              f"x={gx0:.2f} y={gy0:.2f} w={gw0:.2f} h={gh0:.2f}")
        print(f"注入时反算全局墨迹盒     = "
              f"x={inj_x:.2f} y={inj_y:.2f} w={inj_w:.2f} h={inj_h:.2f}")
        print("\n落点偏差（注入 − 已 settled）:")
        print(f"  dx = {dx:+.3f}  逻辑像素")
        print(f"  dy = {dy:+.3f}  逻辑像素")
        print(f"  dw = {dw:+.3f}  逻辑像素")
        print(f"  dh = {dh:+.3f}  逻辑像素")
        max_abs = max(abs(dx), abs(dy), abs(dw), abs(dh))
        print(f"  max|delta| = {max_abs:.3f}  逻辑像素 "
              f"（目标 ≤ 2.0）")

        try:
            splash._close()
        except Exception:                     # noqa: BLE001
            pass
        win.close()
        return 0
    finally:
        # ⛔ 还原 QSettings 快照（新键删除、避免析构回写）
        st2 = QSettings(_ORG, _APP)
        for k in list(st2.allKeys()):
            if k not in SNAP:
                st2.remove(k)
        for k, v in SNAP.items():
            st2.setValue(k, v)
        st2.sync()
        del st2


if __name__ == "__main__":
    sys.exit(main())
