# -*- coding: utf-8 -*-
r"""生成 README 用的界面截图（真实数据、真实窗口）。

产出（写进 docs/images/，README 直接引用）：
  main-window.png   深色主题 + 一轮真实形态的 BMS 数据（有越界、有 BMS ERROR）
  main-light.png    浅色主题同一份数据
  ble-mode.png      「连接方式 = 蓝牙广播」的配置卡（假设备列表，演示用）

⚠️ 结束时把用户的 QSettings 快照还原（跑过带窗口的脚本必须做，踩过两次）。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

def capture_window(widget, path: str) -> bool:
    """抓窗口：必须先把窗口挪到**屏幕外**再 grab()。

    毛玻璃卡片要采样背后内容，窗口压在别的窗口/屏幕上时采样不到，抓出来
    卡片全黑（踩过）。_render_check.py 的做法是 setGeometry(-4000,-4000)，
    这里沿用 —— 那份脚本出的图一直是对的。
    """
    widget.setGeometry(-4000, -4000, widget.width(), widget.height())
    from PySide6.QtWidgets import QApplication
    deadline = __import__("time").time() + 1.2
    while __import__("time").time() < deadline:
        QApplication.processEvents()
        __import__("time").sleep(0.02)
    return widget.grab().save(path, "PNG")


OUT = os.path.join(_ROOT, "docs", "images")


def round_text() -> str:
    """一轮真实形态的数据：电压 25 点（1 个超上限、1 个欠压）+ 温度 8 点 + BMS。"""
    volts = [f"{i:02d}.{3700 + (i * 37) % 500}" for i in range(1, 26)]
    volts[7] = "08.4990"        # 超上限（红）
    volts[18] = "19.2100"       # 欠压（绿）
    temps = [f"{i:02d}.{220 + (i * 13) % 60}" for i in range(1, 9)]
    temps[3] = "04.9100"        # 越界（黄）
    return (
        "[2026-10-15 10:24:31]\n"
        + " ".join(volts) + "\n"
        + "Temperature1:\n"
        + " ".join(temps) + "\n"
        + "BMS ERROR\n"
        + "21. CELL OVERVOLTAGE\n22. CELL UNDERVOLTAGE\n23. TEMP HIGH\n"
    )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("CQUFSAE串口调试助手")
    app.setOrganizationName("MimoWorks")
    app.setStyle("Fusion")

    settings = QSettings("MimoWorks", "SerialAssistant")
    # 快照要还原的用户配置（跑窗口脚本后必须还原，历史教训）
    keep = {k: settings.value(k) for k in settings.allKeys()}

    import customization
    from ui_theme import apply_scheme, ui_font
    from main_window import MainWindow, LINK_BLE

    # README 截图要展示"完整默认界面"——先把用户本机的定制全部重置成默认
    # （隐藏了发送区/配置卡的话截图就是半个窗口），结束时由 finally 还原。
    for spec in customization.specs():
        customization.set(spec["key"], spec["default"])

    def wait_rx(window, seconds=3.0):
        """等收包管线把缓存刷进接收区（30ms 成帧 + 200ms 兜底 + 半行缓发）。"""
        end = __import__("time").time() + seconds
        while __import__("time").time() < end:
            app.processEvents()
            if window.txt_rx.toPlainText().strip():
                return True
            __import__("time").sleep(0.02)
        return bool(window.txt_rx.toPlainText().strip())

    app.setFont(ui_font(12.5))

    os.makedirs(OUT, exist_ok=True)
    data = round_text().encode("utf-8")

    try:
        # ---- 深色（默认）----
        apply_scheme("dark")
        window = MainWindow()
        window._port_watch.stop()
        window.resize(1280, 832)
        window.show()
        app.processEvents()
        if getattr(window, "_expanded", False):     # 上次留在"接收区展开"态的话收回来
            window._collapse_receive()              # README 要的是默认布局
        app.processEvents()
        for _ in range(2):
            window._on_received(data)          # 两轮，时间戳/着色/网格都走真管线
        assert wait_rx(window), "接收区没有等到数据（缓发超时）"
        app.processEvents()
        capture_window(window, os.path.join(OUT, "main-window.png"))
        print("  main-window.png")

        # ---- 蓝牙广播模式（配置卡换装 + 演示设备列表）----
        # 切模式会自动开一轮真扫描（3.5s）；先等它自己收尾，**再**注入演示设备
        # 并刷列表 —— 顺序反了会被真扫描把假数据清掉，状态栏还会写"没扫到"。
        window.cmb_link.setCurrentText(LINK_BLE)
        deadline = __import__("time").time() + 6
        while __import__("time").time() < deadline and window._ble_scanner:
            app.processEvents()
            __import__("time").sleep(0.02)
        if window._ble_scanner:
            window._finish_ble_scan()
        window._ble_target = ""          # 别让上次选的设备盖过演示列表的第一项
        window._ble_seen = {
            "AA:BB:CC:DD:EE:01": {"address": "AA:BB:CC:DD:EE:01",
                                  "name": "CQUFSAE-BMS", "rssi": -41,
                                  "payload": b"Voltage1: 01.3700\n"},
            "AA:BB:CC:DD:EE:02": {"address": "AA:BB:CC:DD:EE:02",
                                  "name": "未命名", "rssi": -67, "payload": None},
        }
        window._fill_ble_combo()
        app.processEvents()
        capture_window(window, os.path.join(OUT, "ble-mode.png"))
        print("  ble-mode.png")
        window.close()
        window.deleteLater()
        app.processEvents()

        # ---- 浅色 ----
        apply_scheme("light")
        window = MainWindow()
        window._port_watch.stop()
        window.resize(1280, 832)
        window.show()
        app.processEvents()
        if getattr(window, "_expanded", False):
            window._collapse_receive()
        app.processEvents()
        window._on_received(data)
        assert wait_rx(window), "浅色：接收区没有等到数据"
        app.processEvents()
        capture_window(window, os.path.join(OUT, "main-light.png"))
        print("  main-light.png")
        window.close()
        window.deleteLater()
        app.processEvents()
    finally:
        # 无条件还原用户配置（自测的自我固化教训：不能信"程序退出会自己恢复"）
        for key, value in keep.items():
            if value is not None:
                settings.setValue(key, value)
        settings.sync()

    print("完成 →", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
