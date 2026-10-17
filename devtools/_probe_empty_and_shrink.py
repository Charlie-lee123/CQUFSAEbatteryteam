# -*- coding: utf-8 -*-
r"""验证两件事（用户 10:02 提的）：

1. 发送区**已折叠**时，最大化要让它**同步缩到 0**（和串口配置卡同一套 Reveal、
   同样 220ms），而不是"收一下再突然消失"；且轨迹必须单调、无鼓包。

用法：python devtools\_probe_empty_and_shrink.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings                  # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402

OUT = os.path.join(_ROOT, "_check")


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    import customization
    from ui_theme import apply_scheme, ui_font
    from main_window import MainWindow

    for spec in customization.specs():
        customization.set(spec["key"], spec["default"])
    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    window = MainWindow()
    window._port_watch.stop()
    screen = app.primaryScreen().availableGeometry()
    window.setGeometry(screen.x() + 60, screen.y() + 50, 1280, 900)
    window.show()
    window.raise_()
    for _ in range(120):
        app.processEvents()
    time.sleep(0.5)
    for _ in range(40):
        app.processEvents()

    print(f"发送区初始：collapsed={window._tx_collapsed}  高={window._tx_card.height()}")

    # ---- 2) 已折叠的发送区：最大化时逐帧缩到 0 ----------------------------
    samples = []
    window._expand_receive()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 0.45:
        samples.append((round((time.perf_counter() - t0) * 1000),
                        window._tx_card.height(),
                        window._tx_card.isVisible(),
                        window._config_card.width()))
        app.processEvents()
        time.sleep(0.0015)
    print("  发送区高度轨迹 (ms, 高, 可见) 与配置卡宽度（对照）：")
    print("   ", samples[::6][:12])
    heights = [s[1] for s in samples]
    print(f"   发送区: {heights[0]} → {min(heights)} → 终态 {heights[-1]}，"
          f"卡片可见={samples[-1][2]}")
    config_w = [s[3] for s in samples]
    print(f"   配置卡: {config_w[0]} → {min(config_w)} → {config_w[-1]}")
    # 两者"消失"的时刻（可见性翻 False 的那一拍）
    tx_gone = next((s[0] for s in samples if not s[2]), None)
    cfg_gone = next((s[0] for s in samples if s[3] <= 0.15 * samples[0][3]), None)
    print(f"   消失时刻：发送区 t={tx_gone}ms  配置卡 t={cfg_gone}ms   "
          f"差={abs((tx_gone or 0) - (cfg_gone or 0))}ms")

    for _ in range(120):
        app.processEvents()
        time.sleep(0.004)
    time.sleep(0.3)
    for _ in range(60):
        app.processEvents()

    window._collapse_receive()
    for _ in range(250):
        app.processEvents()
        time.sleep(0.004)
    print(f"收起后：发送区高={window._tx_card.height()} "
          f"collapsed={window._tx_collapsed} 最小高={window._tx_card.minimumHeight()}")

    window.setGeometry(0, 0, 1134, 820)
    window.close()
    for key, value in snapshot.items():
        if value is not None:
            settings.setValue(key, value)
    settings.sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())