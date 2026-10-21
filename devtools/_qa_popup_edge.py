# -*- coding: utf-8 -*-
r"""QA 独立探针：蓝牙下拉贴屏幕右缘/下缘时，弹层是否越界（只读验收用）。

与 devtools/_probe_ble_popup.py 的区别：那个把窗口摆在屏幕中间（什么都放得下），
本探针把主窗口**顶到屏幕右边缘**，模拟用户真实场景（窗口靠右、下拉在右半边）。

断言：
  1. 弹层 x + width <= 屏幕可用区右边界；
  2. 弹层 y + height <= 屏幕可用区下边界；
  3. 逐帧高度单调不减（允许 0 次回缩）、首帧 opacity <= 0.05。

用法：QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_qa_popup_edge.py
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QPoint, QRect, QSettings                # noqa: E402
from PySide6.QtWidgets import QApplication                         # noqa: E402

DEVICES = (
    ("C8:2B:96:1A:44:07", "CQUFSAE-BMS", -46, True),
    ("D4:11:22:8E:90:1C", "BMS-Broadcast", -71, True),
    ("F0:9E:4C:33:7A:52", "未命名", -88, False),
    ("A4:C1:38:9F:2E:11", "CQUFSAE-BMS-备用箱（后箱）", -63, True),
)

FAILS: list[str] = []


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


def fill(cmb) -> None:
    cmb.blockSignals(True)
    cmb.clear()
    pad = "（超长名字用于验证省略号" + "长" * 220 + "）" if os.environ.get("QA_LONG") else ""
    for addr, name, rssi, payload in DEVICES:
        note = "有广播负载" if payload else "无负载"
        cmb.addItem(f"{addr} — {name}{pad} — {rssi}dBm（{note}）", addr)
    cmb.blockSignals(False)


def open_and_measure(app, window, combo, label: str, want_corner: str,
                     literal: tuple | None = None) -> None:
    """把窗口顶到指定角落 → 打开弹层 → 断言几何。"""
    screen = combo.screen().availableGeometry()
    win_w = max(window.minimumWidth(), 1180)
    win_h = min(940, screen.height() - 40)
    x = screen.x() + screen.width() - win_w if "right" in want_corner else screen.x()
    y = screen.y() + screen.height() - win_h if "bottom" in want_corner else screen.y()
    if literal is not None:                      # 按 lead 原话的几何（宽 240 会被最小值夹住）
        x, y, win_w, win_h = literal
        win_w = max(win_w, window.minimumWidth())
    window.setGeometry(x, y, win_w, win_h)
    pump(app, 0.5)
    anchor = combo.mapToGlobal(QPoint(0, 0))
    print(f"\n=== {label} ===")
    print(f"屏幕可用区 {screen.x()},{screen.y()} {screen.width()}x{screen.height()}")
    print(f"主窗口     {window.geometry().x()},{window.geometry().y()} "
          f"{window.width()}x{window.height()}")
    print(f"下拉控件   {anchor.x()},{anchor.y()} {combo.width()}x{combo.height()}")

    import ui_anim
    ticker = ui_anim._ticker()
    orig = ticker._tick
    samples: list[tuple[float, QRect, float]] = []
    t0 = time.perf_counter()

    def patched():
        orig()
        c = combo.view().window()
        samples.append((round((time.perf_counter() - t0) * 1000, 1),
                        QRect(c.geometry()), round(c.windowOpacity(), 3)))

    ticker._tick = patched
    try:
        combo.showPopup()
        # showPopup() **返回的那一瞬间**（还没进事件循环）就取样：这正是老代码
        # "满高不透明闪一帧" 的窗口期。新代码要求这里已经是 起始高度 + 全透明。
        c0 = combo.view().window()
        print(f"同步态     h={c0.geometry().height()} "
              f"opacity={c0.windowOpacity():.2f} (showPopup 刚返回)")
        if c0.windowOpacity() > 0.05:
            FAILS.append(f"{label}: showPopup 返回时 opacity="
                         f"{c0.windowOpacity()} —— 会先闪一帧")
        pump(app, 0.40)
    finally:
        ticker._tick = orig

    container = combo.view().window()
    g = container.geometry()
    right_over = g.x() + g.width() - (screen.x() + screen.width())
    bottom_over = g.y() + g.height() - (screen.y() + screen.height())
    left_over = screen.x() - g.x()
    top_over = screen.y() - g.y()
    print(f"弹层最终   {g.x()},{g.y()} {g.width()}x{g.height()} "
          f"opacity={container.windowOpacity():.2f}")
    print(f"越界      右{right_over:+d} 下{bottom_over:+d} 左{left_over:+d} 上{top_over:+d}")
    if right_over > 0:
        FAILS.append(f"{label}: 弹层右缘出屏 {right_over}px")
    if bottom_over > 0:
        FAILS.append(f"{label}: 弹层下缘出屏 {bottom_over}px")
    if left_over > 0:
        FAILS.append(f"{label}: 弹层左缘出屏 {left_over}px")
    if top_over > 0:
        FAILS.append(f"{label}: 弹层上缘出屏 {top_over}px")

    first = samples[0] if samples else None
    if first is None:
        FAILS.append(f"{label}: 没采到任何帧")
    else:
        print(f"首帧       h={first[1].height()} opacity={first[2]:.2f} "
              f"(t={first[0]}ms)")
        if first[2] > 0.05:
            FAILS.append(f"{label}: 首帧 opacity={first[2]} 不是 0（会闪一下）")
        drops = [(s[0], s[1].height(), prev[1].height())
                 for prev, s in zip(samples, samples[1:]) if s[1].height() < prev[1].height()]
        print("高度序列   " + " → ".join(str(s[1].height()) for s in samples[:14])
              + (" …" if len(samples) > 14 else ""))
        if drops:
            FAILS.append(f"{label}: 高度出现回缩 {drops[:3]}")
        print(f"最终行高   {samples[-1][1].height()}（期望 == 弹层最终高度 {g.height()}）")
        if samples[-1][1].height() != g.height():
            FAILS.append(f"{label}: 末帧高度 {samples[-1][1].height()} != {g.height()}")
    # 截图（条目超长时应看到省略号，而不是被硬裁半个字）—— 必须在 hidePopup 之前拍
    shot_path = os.path.join(_ROOT, "_check", "qa_ble_popup_edge.png")
    os.makedirs(os.path.dirname(shot_path), exist_ok=True)
    screen_shot = app.primaryScreen().grabWindow(0)
    dpr = screen_shot.devicePixelRatio() or 1.0
    physical = QRect(int(g.x() * dpr), int(g.y() * dpr),
                     int(g.width() * dpr), int(g.height() * dpr))
    screen_shot.copy(physical).save(shot_path)
    print(f"截图       {shot_path} {physical.size()} (dpr={dpr})")
    combo.hidePopup()
    pump(app, 0.2)


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
        window.cmb_link.setCurrentText("蓝牙广播")
        pump(app, 0.5)
        window._finish_ble_scan(fill=False)
        fill(window.cmb_port)
        pump(app, 0.2)

        open_and_measure(app, window, window.cmb_port, "蓝牙下拉 @ 屏幕右下角",
                         "right,bottom")
        open_and_measure(app, window, window.cmb_port, "蓝牙下拉 @ 屏幕右上角",
                         "right")
        # lead 原话：setGeometry(screen.width()-260, 200, 240, 800)
        # （主窗口最小宽 1134，实际会被夹成 1134 宽、右半边出屏 —— 更极端）
        open_and_measure(app, window, window.cmb_port,
                         "蓝牙下拉 @ lead 原话几何（窗口右半出屏）", "right",
                         literal=(window.screen().availableGeometry().width() - 260,
                                  200, 240, 800))
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
        settings.sync()

    print("\n================ QA 判定 ================")
    if FAILS:
        for item in FAILS:
            print("FAIL  " + item)
        return 1
    print("PASS  弹层在屏幕右缘/下缘均未越界；首帧透明、高度单调不减")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
