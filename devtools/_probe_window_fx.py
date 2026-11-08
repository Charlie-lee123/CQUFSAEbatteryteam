# -*- coding: utf-8 -*-
r"""窗口级动画探针：打开 / 最小化 / 还原 / 最大化不要"一帧直接变"。

用户 2026-10-31：「打开软件、最小化、还原的时候不要一帧直接变了，要连续和软件内
动画一样流畅」。三处都走 ``setWindowOpacity``（DWM 合成，不是被禁的
QGraphicsOpacityEffect），本探针逐帧采样窗口不透明度，确认：

1. 打开：0 → 1，连续多个台阶，收尾正好 1.0（layered 必须摘掉）。
2. 最小化：先淡出到 ~0，再 isMinimized()。
3. 还原：不透明度从 0 爬回 1.0。
4. 最大化（全屏）：淡入后不透明度回到 1.0，且任务栏区域仍是应用自己的像素
   （窗口变 layered 不能把"全屏盖住任务栏"搞坏）。

用法：.venv\Scripts\python.exe devtools\_probe_window_fx.py
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings                    # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

OUT = os.path.join(_ROOT, "_check")


def _pump(app: QApplication, rounds: int = 40) -> None:
    for _ in range(rounds):
        app.processEvents()


def _sample(app: QApplication, window, ms: int = 400) -> list[tuple[int, float, bool]]:
    """逐帧采样窗口不透明度（相对起始时刻的毫秒数）。"""
    samples: list[tuple[int, float, bool]] = []
    begin = time.perf_counter()
    while (time.perf_counter() - begin) * 1000 < ms:
        app.processEvents()
        samples.append((int((time.perf_counter() - begin) * 1000),
                        round(window.windowOpacity(), 3),
                        window.isMinimized()))
        time.sleep(0.008)
    return samples


def _steps(samples) -> int:
    """不透明度出现过多少个不同的值（>2 说明是连续走的，不是"一帧到位"）。"""
    return len({value for _, value, _ in samples})


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

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
        window.setGeometry(120, 100, 1000, 900)
        window.show()
        window.raise_()
        _pump(app, 80)
        time.sleep(0.4)
        _pump(app, 40)

        print("== 打开（淡入）==")
        window.setWindowOpacity(1.0)
        window._reveal_window(0.0, 150)
        opening = _sample(app, window, 320)
        opening_steps = _steps(opening)
        print(f"  不透明度轨迹 {[v for _, v, _ in opening[::4]]}")
        print(f"  台阶数 {opening_steps}，收尾 {opening[-1][1]}")
        if opening_steps < 4:
            problems.append(f"打开只走了 {opening_steps} 个台阶（还是'一帧直接变'）")
        if abs(opening[-1][1] - 1.0) > 1e-3:
            problems.append(f"打开收尾不透明度 {opening[-1][1]}，应为 1.0")

        print("\n== 最小化（先淡出再收进任务栏）==")
        window._minimize_animated()
        closing = _sample(app, window, 420)
        print(f"  不透明度轨迹 {[v for _, v, _ in closing[::4]]}")
        print(f"  最终 opacity={window.windowOpacity():.3f} "
              f"minimized={window.isMinimized()}")
        if _steps(closing) < 3:
            problems.append("最小化没有淡出过程")
        if not window.isMinimized():
            problems.append("最小化没有真的收进任务栏")

        print("\n== 还原（淡入）==")
        window.showNormal()
        window.setGeometry(120, 100, 1000, 900)
        restoring = _sample(app, window, 420)
        print(f"  不透明度轨迹 {[v for _, v, _ in restoring[::4]]}")
        print(f"  收尾 {restoring[-1][1]}")
        if abs(restoring[-1][1] - 1.0) > 1e-3:
            problems.append(f"还原收尾不透明度 {restoring[-1][1]}，应为 1.0")
        if _steps(restoring) < 3:
            problems.append("还原没有淡入过程")

        print("\n== 最大化（全屏淡入 + 任务栏仍被盖住）==")
        window.startup_fullscreen()
        window._want_fullscreen = True       # 恢复成"用户想要全屏"
        _pump(app, 60)
        time.sleep(0.5)
        deep = _sample(app, window, 260)
        print(f"  全屏后 opacity={window.windowOpacity():.3f} "
              f"isFullScreen={window.isFullScreen()} geo={window.geometry().getRect()}")
        if abs(deep[-1][1] - 1.0) > 1e-3:
            problems.append(f"全屏后不透明度 {deep[-1][1]}，应为 1.0")
        screen = app.primaryScreen().geometry()
        shot = app.primaryScreen().grabWindow(
            0, screen.center().x() - 200, screen.bottom() - 30, 400, 30)
        path = os.path.join(OUT, "snap_taskbar_strip.png")
        shot.save(path)
        image = shot.toImage()
        colours = {image.pixelColor(x, 8).name()
                   for x in range(0, image.width(), 20)}
        print(f"  屏幕最底部 30px 的颜色集合 {sorted(colours)[:5]} → {path}")
        # 任务栏是浅色/半透明的一整条：出现大量与页面底色不同的浅色说明没盖住
        if any(int(name[1:3], 16) > 0x60 for name in colours):
            problems.append(f"任务栏可能没被盖住（底部出现浅色 {sorted(colours)[:3]}）")

        print("\n== 结论 ==")
        print("  全部通过" if not problems else "  ⛔ " + "；".join(problems))
    finally:
        if window is not None:
            window._want_fullscreen = False
            window.setWindowOpacity(1.0)
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
