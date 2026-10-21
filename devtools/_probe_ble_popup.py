# -*- coding: utf-8 -*-
r"""蓝牙设备下拉：弹层几何体检（是否出屏 / 开合动画是否有跳变）。

用户反馈两条：「蓝牙设备选取的动画要跳变」「部分显示到屏幕外」。
这里量三件事：
  1. 弹层容器的最终矩形 vs 屏幕可用区域 —— 有没有越界（左/右/上/下）；
  2. showPopup 之后、动画起跑之前的那一帧窗口几何（"先满高闪一下再缩回去"）；
  3. 逐帧容器高度/透明度（看有没有非单调的跳）。

用法：python devtools/_probe_ble_popup.py
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QPoint, QSettings                        # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

DEVICES = (
    ("C8:2B:96:1A:44:07", "CQUFSAE-BMS", -46, True),
    ("D4:11:22:8E:90:1C", "BMS-Broadcast", -71, True),
    ("F0:9E:4C:33:7A:52", "未命名", -88, False),
    ("A4:C1:38:9F:2E:11", "CQUFSAE-BMS-备用箱", -63, True),
)


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))

    settings = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: settings.value(k) for k in settings.allKeys()}
    settings.remove("geometry")

    from main_window import MainWindow
    window = MainWindow()
    try:
        window._port_watch.stop()
        window.show()
        window.apply_startup_state()
        pump(app, 1.0)
        window.resize(1180, 940)
        pump(app, 0.4)
        window.cmb_link.setCurrentText("蓝牙广播")
        pump(app, 0.6)
        window._finish_ble_scan(fill=False)
        window.cmb_port.blockSignals(True)
        window.cmb_port.clear()
        for addr, name, rssi, payload in DEVICES:
            note = "有广播负载" if payload else "无负载"
            window.cmb_port.addItem(f"{addr} — {name} — {rssi}dBm（{note}）", addr)
        window.cmb_port.blockSignals(False)
        pump(app, 0.2)

        cmb = window.cmb_port
        screen = cmb.screen().availableGeometry()
        anchor = cmb.mapToGlobal(QPoint(0, 0))
        print(f"屏幕可用区 {screen.x()},{screen.y()} {screen.width()}x{screen.height()}")
        print(f"下拉控件   {anchor.x()},{anchor.y()} {cmb.width()}x{cmb.height()}")

        # ---- 逐帧采样弹层窗口几何 ----
        samples: list[tuple[float, int, int, int, int, float]] = []
        t0 = time.perf_counter()
        import ui_anim
        ticker = ui_anim._ticker()
        orig = ticker._tick

        def patched():
            orig()
            container = cmb.view().window()
            g = container.geometry()
            samples.append((round((time.perf_counter() - t0) * 1000, 1),
                            g.x(), g.y(), g.width(), g.height(),
                            round(container.windowOpacity(), 3)))

        ticker._tick = patched
        try:
            cmb.showPopup()
            pump(app, 0.45)
        finally:
            ticker._tick = orig

        container = cmb.view().window()
        g = container.geometry()
        print(f"弹层最终   {g.x()},{g.y()} {g.width()}x{g.height()}  opacity="
              f"{container.windowOpacity():.2f}")
        right = g.x() + g.width()
        bottom = g.y() + g.height()
        print(f"越界检查   右 {right - (screen.x() + screen.width())}px  "
              f"下 {bottom - (screen.y() + screen.height())}px  "
              f"左 {screen.x() - g.x()}px  上 {screen.y() - g.y()}px")

        print("\n逐帧（t, x, y, w, h, opacity）")
        last_h = None
        for row in samples:
            mark = ""
            if last_h is not None:
                delta = row[4] - last_h
                if delta < -4:
                    mark = f"  ← 高度回缩 {delta:+d}"
            last_h = row[4]
            print(f"  {row[0]:>6.1f} {row[1]:>5} {row[2]:>5} {row[3]:>4} {row[4]:>4} "
                  f"{row[5]:.2f}{mark}")
        cmb.hidePopup()
        pump(app, 0.2)
    finally:
        window.setGeometry(0, 0, 1134, 820)
        window.close()
        app.processEvents()
        for key in settings.allKeys():
            if key not in snap:
                settings.remove(key)
        for key, value in snap.items():
            if value is None:
                settings.remove(key)
            else:
                settings.setValue(key, value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
