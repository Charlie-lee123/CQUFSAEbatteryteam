# -*- coding: utf-8 -*-
r"""验证 Win11 Snap Layouts 的 HTMAXBUTTON 命中（用户要的 1/2、1/3、1/4 分屏）。

Win11 是这么判断"鼠标压在最大化键上"的：窗口对 ``WM_NCHITTEST`` 回 ``HTMAXBUTTON``。
系统标题栏天然会回；我们的标题栏是自绘的（无边框窗口），原来整片客户区只回
``HTCLIENT`` —— 所以悬停最大化键不弹面板、Win+Z 也拿不到。

本探针做四件事（都靠**真实光标位置**，因为我们的实现读的是 GetCursorPos）：
1. 把光标挪到最大化键中心 → SendMessage(WM_NCHITTEST) 必须回 9。
2. 把光标挪到日志区中间 → 必须回 1（HTCLIENT），不能误伤其它区域。
3. 光标挪到窗口左边缘 → 必须回 10/11/12/13（HTLEFT…HTBOTTOMRIGHT 那一套），
   也就是原来的边缘缩放命中带还在（那段走的是 Qt 的 startSystemResize，不受影响）。
4. 真悬停 1 秒，截屏看分屏面板有没有弹出来（存 _check/snap_flyout.png 供肉眼确认）。

用法：.venv\Scripts\python.exe devtools\_probe_snap_hittest.py
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings                    # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

OUT = os.path.join(_ROOT, "_check")
WM_NCHITTEST = 0x0084
HTNAMES = {0: "HTNOWHERE", 1: "HTCLIENT", 2: "HTCAPTION", 9: "HTMAXBUTTON",
           10: "HTLEFT", 11: "HTRIGHT", 12: "HTTOP", 13: "HTTOPLEFT",
           14: "HTTOPRIGHT", 15: "HTBOTTOM", 16: "HTBOTTOMLEFT",
           17: "HTBOTTOMRIGHT"}


def _pump(app: QApplication, rounds: int = 60) -> None:
    for _ in range(rounds):
        app.processEvents()


def _hit_test(hwnd: int, x: int, y: int) -> int:
    """按**物理屏幕坐标**发一次 WM_NCHITTEST，返回系统的命中码。"""
    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.06)
    lparam = (int(y) << 16) | (int(x) & 0xFFFF)
    result = user32.SendMessageW(wintypes.HWND(hwnd), WM_NCHITTEST,
                                 wintypes.WPARAM(0), wintypes.LPARAM(lparam))
    return int(result)


def _press_win_arrow(user32, vk: int) -> None:
    """按一次 Win+方向键（keybd_event；只影响前台窗口，测完不动别的）。"""
    vk_lwin, keyup = 0x5B, 0x0002
    user32.keybd_event(vk_lwin, 0, 0, 0)
    time.sleep(0.06)
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.06)
    user32.keybd_event(vk, 0, keyup, 0)
    time.sleep(0.06)
    user32.keybd_event(vk_lwin, 0, keyup, 0)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    import customization
    from ui_theme import apply_scheme, ui_font
    from main_window import MainWindow

    user32 = ctypes.windll.user32
    saved_pos = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(saved_pos))
    window = None
    failures: list[str] = []
    try:
        for spec in customization.specs():
            customization.set(spec["key"], spec["default"])
        app.setFont(ui_font(12.5))
        apply_scheme("dark")

        window = MainWindow()
        window._port_watch.stop()
        window._want_fullscreen = False
        window._expect_normal = True
        window.showNormal()
        window.setGeometry(120, 100, 1000, 900)
        window.show()
        window.raise_()
        window.activateWindow()
        _pump(app, 80)
        time.sleep(0.4)
        _pump(app, 40)

        hwnd = int(window.winId())
        dpr = window.devicePixelRatioF()
        origin = window.mapToGlobal(window.rect().topLeft())

        def to_physical(point) -> tuple[int, int]:
            return (int(round((origin.x() + point.x()) * dpr)),
                    int(round((origin.y() + point.y()) * dpr)))

        button = window._caption_bar.btn_max
        button_c = to_physical(button.geometry().center() + window._caption_bar.pos())
        log_c = to_physical(window.txt_rx.geometry().center()
                            + window._rx_card.pos() + window._caption_bar.pos())
        left_edge = to_physical(window.rect().topLeft())  # 左上角 → HTTOPLEFT

        cases = [
            ("最大化键中心", button_c, 9, "HTMAXBUTTON"),
            ("日志区中心", log_c, 1, "HTCLIENT"),
            # 边缘**故意**保持 HTCLIENT：无边框窗口没有 WS_THICKFRAME，系统的缩放
            # 命中带本来就不存在，边缘拖拽是 Qt 那侧用 startSystemResize 接的
            # （见 main_window.eventFilter）。这里断言"没被我们的 hook 改成别的"。
            ("窗口左上角", (left_edge[0] + 1, left_edge[1] + 1), 1, "HTCLIENT"),
        ]
        print("== WM_NCHITTEST 命中 ==")
        for label, point, want, want_name in cases:
            got = _hit_test(hwnd, *point)
            ok = got == want
            print(f"  {label:<12} 物理({point[0]},{point[1]}) → {got} "
                  f"({HTNAMES.get(got, '?')})  期望 {want} ({want_name})"
                  f"  {'✓' if ok else '✗'}")
            if not ok:
                failures.append(f"{label} 命中 {got}，期望 {want}")

        # 悬停 1.2 秒后截屏：看系统的分屏面板有没有弹出来（肉眼确认用）
        os.makedirs(OUT, exist_ok=True)
        user32.SetCursorPos(*button_c)
        for _ in range(140):
            app.processEvents()
            time.sleep(0.01)
        print(f"  悬停后按钮热态 _max_btn_hot = {window._max_btn_hot}")
        if not window._max_btn_hot:
            failures.append("悬停后按钮没有进入热态（悬停反馈会丢）")
        # 截屏要的是**逻辑**坐标（grabWindow 收的是 device-independent 像素），
        # 前面算的 button_c 是物理像素 —— 这里重新按逻辑坐标算一次。
        logical_button = window.mapToGlobal(
            button.geometry().center() + window._caption_bar.pos())
        shot = app.primaryScreen().grabWindow(
            0, logical_button.x() - 300, logical_button.y() - 10, 600, 420)
        path = os.path.join(OUT, "snap_flyout.png")
        shot.save(path)
        print(f"  悬停截图 {path}（分屏面板若弹出就长在这张图里）")

        # ---- 真的分屏一次：Win+← 应该把窗口摆成左半屏 --------------------
        # ⚠️ 这条是**落地证据**：只回 HTMAXBUTTON 但不给 WS_THICKFRAME 的话，
        #    shell 认为窗口不可缩放，Win+← 一动不动（实测过）。
        print("\n== Win+← 分屏 ==")
        before = window.geometry()
        _press_win_arrow(user32, 0x25)          # VK_LEFT
        _pump(app, 80)
        time.sleep(0.9)
        _pump(app, 60)
        after = window.geometry()
        print(f"  分屏前 {before.getRect()} → 分屏后 {after.getRect()}")
        screen_w = app.primaryScreen().geometry().width()
        if after.width() >= before.width() or after.width() > int(screen_w * 0.7):
            failures.append(f"Win+← 没有把窗口摆成半屏（{before.getRect()} → {after.getRect()}）")
        # 还原成普通窗口，别把用户窗口留在半屏
        window._want_fullscreen = False
        window._expect_normal = True
        window.showNormal()
        window.setGeometry(before)
        _pump(app, 40)

        print("\n== 结论 ==")
        print("  全部通过" if not failures else "  ⛔ " + "；".join(failures))
    finally:
        user32.SetCursorPos(saved_pos.x, saved_pos.y)
        if window is not None:
            window.close()
            window.deleteLater()
        _pump(app, 30)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        settings.sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
