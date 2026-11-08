# -*- coding: utf-8 -*-
r"""分屏（Snap）适配探针：量最小宽度、按四种分屏宽度截图并自检。

用户 2026-10-31：「我还需要串口软件可以适配 windows 的两分屏三分屏和四分屏」。
1920 宽的屏幕上 1/2=960、1/3=640、1/4=480，窗口必须真能摆进这些尺寸。

本探针做三件事：
1. 打窗口的最小宽高 + 各主要控件的最小尺寸 —— 定位"谁把窗口顶宽了"。
2. 按 1920 / 960 / 640 / 480 × 1040 四种尺寸摆窗口并 grab() 到
   ``_check/snap_<宽>.png``，打印实际落到的尺寸（被顶宽会标出来）。
3. 几何自检：每张图按像素采样确认卡片真的画出来了（不是一片背景），
   并检查最大化的那颗键、配置卡、日志区都在窗口内。

用法：.venv\Scripts\python.exe devtools\_probe_snap.py [--no-shot]
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings, QSize           # noqa: E402
from PySide6.QtGui import QColor                        # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

OUT = os.path.join(_ROOT, "_check")
# 从宽到窄，和用户真的去拖分屏的顺序一致。1920 屏上是 960/640/480，
# 本机桌面只有 1600 逻辑宽，1/4 是 400 —— 一并量进来（最窄的那一档）
SIZES = (1920, 960, 640, 480, 400)
HEIGHT = 1040
BACKDROP = QColor("#0d0d0f")        # 页面底色：采样点等于它说明卡片没画出来


def _pump(app: QApplication, rounds: int = 60) -> None:
    for _ in range(rounds):
        app.processEvents()


def _settle(app: QApplication) -> None:
    """让布局/重绘真正落地（窄档切换会重排两层布局）。"""
    _pump(app, 40)
    time.sleep(0.15)
    _pump(app, 40)


def _dump_mins(window) -> None:
    print("== 最小尺寸 ==")
    print(f"  window.minimumSizeHint = {window.minimumSizeHint()}")
    print(f"  window.minimumSize     = {window.minimumSize()}")
    targets = [
        ("caption_bar", "_caption_bar"), ("brand_header", "brand_header"),
        ("rx_card", "_rx_card"), ("tx_card", "_tx_card"),
        ("config_card", "_config_card"), ("config_content", "_config_content"),
        ("rx_rail", "_rx_rail"), ("splitter", "_splitter"),
        ("txt_rx", "txt_rx"), ("rx_opts_row", "_rx_opts_row"),
        ("tx_opts_row", "_tx_opts_row"),
    ]
    for label, attr in targets:
        widget = getattr(window, attr, None)
        if widget is None:
            continue
        hint = widget.minimumSizeHint()
        print(f"  {label:<15} minHint={hint.width():>5}x{hint.height():<5} "
              f"min={widget.minimumWidth():>5}x{widget.minimumHeight():<5} "
              f"hint={widget.sizeHint().width():>5}x{widget.sizeHint().height():<5}")


def _sample(image, point, logical: QSize) -> str:
    """在**窗口坐标** point 处取像素（截图可能是 2 倍像素密度，按比例换算）。"""
    sx = image.width() / max(1, logical.width())
    sy = image.height() / max(1, logical.height())
    x = min(max(0, int(point.x() * sx)), image.width() - 1)
    y = min(max(0, int(point.y() * sy)), image.height() - 1)
    return image.pixelColor(x, y).name()


def main() -> int:
    shot = "--no-shot" not in sys.argv
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    os.makedirs(OUT, exist_ok=True)

    import customization
    from ui_theme import apply_scheme, ui_font
    from main_window import MainWindow

    window = None
    problems: list[str] = []
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
        window.show()
        _pump(app, 80)
        _settle(app)

        _dump_mins(window)

        print("\n== 四种分屏尺寸 ==")
        screen = app.primaryScreen().availableGeometry()
        print(f"  （本机可用桌面 {screen.width()}x{screen.height()} 逻辑像素，"
              f"比它大的目标会被截到桌面大小）")
        for want_w in SIZES:
            target_w = min(want_w, screen.width())
            target_h = min(HEIGHT, screen.height())
            window.resize(target_w, target_h)
            _settle(app)
            got = window.geometry()
            note = ""
            if got.width() > target_w:
                note = f"  ⛔ 被顶宽 +{got.width() - target_w}px"
                problems.append(f"{want_w}px 被顶宽到 {got.width()}px")
            if got.width() < target_w:
                note = f"  ⛔ 没缩到位（差 {target_w - got.width()}px）"
                problems.append(f"{want_w}px 只缩到 {got.width()}px")
            print(f"  目标 {target_w}x{target_h} → 实到 {got.width()}x{got.height()}"
                  f"  档位={window._compact_level}{note}")
            print(f"      接收卡={window._rx_card.geometry().getRect()} "
                  f"配置卡={window._config_card.geometry().getRect()} "
                  f"日志={window.txt_rx.geometry().getRect()}")
            if shot:
                image = window.grab().toImage()
                path = os.path.join(OUT, f"snap_{want_w}.png")
                image.save(path)
                # 采样：日志区中心必须是卡片面（不是页面底色），否则说明卡片没画出来
                card_tl = window._rx_card.geometry().topLeft()
                log_center = window.txt_rx.geometry().center() + card_tl
                colour = _sample(image, log_center, got.size())
                flag = "（= 页面底色，卡片没画出来）" if colour == BACKDROP.name() else ""
                print(f"      截图 {path}  日志区采样={colour}{flag}")
                if colour == BACKDROP.name():
                    problems.append(f"{want_w}px 截图的日志区没画出卡片")

        print("\n== 结论 ==")
        print("  全部通过" if not problems else "  ⛔ " + "；".join(problems))
    finally:
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
