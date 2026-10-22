# -*- coding: utf-8 -*-
r"""重新生成 README 配图（docs/images/*.png）。

为什么要有这个脚本：README 里的图必须是**当前版本的真实界面**。手工截图有三个
老问题 —— 主题/分辨率不统一、改版之后忘了换、以及最尴尬的一种：**文件名和内容
对不上**（`main-light.png` 里装的其实是深色截图，2026-10-18 被用户点出来了）。
所以截图这件事也进脚本：数据是造出来的、主题是显式切的、裁剪矩形是按控件几何
现算的，重跑一次就能拿到一整套一致的图。

用法：
    python devtools/_make_readme_shots.py

产物（全部 2 倍图，跟随本机 dpr）：
    docs/images/main-window.png     主界面（深色）
    docs/images/main-light.png      主界面（浅色）
    docs/images/data-coloring.png   接收区着色（裁切）
    docs/images/ble-mode.png        蓝牙广播模式
    docs/images/settings.png        设置面板（顶部）
    docs/images/vehicle-settings.png 设置面板「车辆（电池箱）」一节
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
from PySide6.QtWidgets import QApplication, QScrollArea            # noqa: E402

OUT = os.path.join(_ROOT, "docs", "images")
WIN_W, WIN_H = 1180, 940

# 一轮真实感的 BMS 数据：电压 mV（上下限 3800/3900）、温度 ℃（上限 60）。
# ⚠️ 温度值的语义是"小数点后的整数部分"（"2.95" = 95 ℃），所以看起来像两位小数
#    的 22/95/61 才是对的，别写成 176 那种假数据。
_BASE = (3737, 3774, 3811, 3848, 3885, 3822, 3859, 3790, 3803, 3870, 3835, 3844,
         3881, 3818, 3855, 3892, 3829, 3866, 3857, 3832, 3869, 3846, 3843, 3880,
         3817)


def _volt_row(values) -> str:
    return " ".join(f"{i + 1:02d}.{v}" for i, v in enumerate(values))


def _with(values, changes) -> tuple:
    out = list(values)
    for index, value in changes.items():
        out[index] = value
    return tuple(out)


ROUNDS = (
    # 第 1 轮：正常为主，3 个欠压（<3800 绿）、2 个超压（>3900 红）
    (_volt_row(_with(_BASE, {10: 3907, 21: 3906})),
     "01.22 02.95 03.38 04.61 05.29 06.33 07.41 08.26",
     "BMS OK"),
    # 第 2 轮：一次 BMS 报警（ERROR 字样标橙 + 明细行原样）
    (_volt_row(_with(_BASE, {2: 3961, 5: 3977, 9: 3958, 19: 3742})),
     "01.28 02.95 03.45 04.61 05.72 06.38 07.44 08.25",
     "BMS ERROR\n21. CELL OVERVOLTAGE\n22. CELL UNDERVOLTAGE\n23. TEMP HIGH"),
)


def pump(app: QApplication, seconds: float = 0.25) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.003)


def restore_settings(settings, snapshot) -> None:
    for key in settings.allKeys():
        if key not in snapshot:
            settings.remove(key)
    for key, value in snapshot.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)


def feed(window, app) -> None:
    """灌三轮数据（每轮按 电压 → 温度 → BMS 给，轮首才会挂时间戳）。"""
    window.chk_timestamp.setChecked(True)
    window._clear_receive()
    pump(app, 0.2)
    for volt, temp, bms in ROUNDS:
        window._emit_block("Voltage1:\n" + volt)
        window._emit_block("Temperature1:\n" + temp)
        window._emit_block(bms)
        pump(app, 0.12)
    pump(app, 0.3)
    # 回到顶部：截图要的是"一轮完整数据"，停在底部只会看到尾巴
    window.txt_rx.verticalScrollBar().setValue(0)
    pump(app, 0.3)


def crop_of(window, widget, pad: int = 6) -> QRect:
    top_left = widget.mapTo(window, QPoint(0, 0))
    return QRect(top_left.x() - pad, top_left.y() - pad,
                 widget.width() + pad * 2, widget.height() + pad * 2)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))

    os.makedirs(OUT, exist_ok=True)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    # 窗口尺寸从注册表恢复，会把下面按坐标裁的图裁歪；README 要的是固定版式。
    settings.remove("geometry")
    # 网格按"自动"（0）：用户的 5 模组 × 25 芯会把一行电压拆成多行，
    # README 要展示的是数据格式里的单行排布。
    for key in ("module_count", "cells_per_module", "temps_per_module"):
        settings.setValue(key, 0)

    from main_window import MainWindow
    from ui_settings import SettingsDialog

    window = MainWindow()
    try:
        window._port_watch.stop()
        window.setGeometry(-4000, -4000, WIN_W, WIN_H)
        window.show()
        pump(app, 1.0)
        window._apply_glass(0)          # 截图不要颗粒，免得压缩后像噪点
        if window._tx_collapsed:        # 展开态才看得到完整版式
            window._toggle_send_area()
            pump(app, 0.6)
        feed(window, app)

        # ---- 主界面：深色 ----
        window._apply_theme("dark")
        pump(app, 0.5)
        window.grab().save(os.path.join(OUT, "main-window.png"))

        # ---- 接收区着色（裁切）：深色下更醒目 ----
        window.grab(crop_of(window, window.txt_rx, 10)).save(
            os.path.join(OUT, "data-coloring.png"))

        # ---- 主界面：浅色 ----
        window._apply_theme("light")
        pump(app, 0.6)
        window.grab().save(os.path.join(OUT, "main-light.png"))

        # ---- 蓝牙广播模式 ----
        window._apply_theme("dark")
        pump(app, 0.3)
        window.cmb_link.setCurrentText("蓝牙广播")
        pump(app, 0.6)
        # 切到广播模式会自动起一次扫描（真机扫到的设备会盖掉下面造的假数据），
        # 先把它停掉；再**直接**往下拉里写条目 —— 不走 _fill_ble_combo()，那条路
        # 会弹 toast、也会被还在跑的扫描信号覆盖（截图里 MAC 变成这台电脑的真实
        # 蓝牙设备就是这么来的）。
        window._finish_ble_scan(fill=False)
        window.cmb_port.blockSignals(True)
        window.cmb_port.clear()
        for addr, name, rssi, payload in (
                ("C8:2B:96:1A:44:07", "CQUFSAE-BMS", -46, True),
                ("D4:11:22:8E:90:1C", "BMS-Broadcast", -71, True),
                ("F0:9E:4C:33:7A:52", "未命名", -88, False)):
            note = "有广播负载" if payload else "无负载"
            window.cmb_port.addItem(f"{addr} — {name} — {rssi}dBm（{note}）", addr)
        window.cmb_port.blockSignals(False)
        window._set_state("蓝牙广播：扫到 3 个设备")
        # 可编辑下拉的默认光标在末尾，条目比框宽时框里显示的是尾巴 —— 拨到开头
        # （产品代码 _select_port 里也补了这一句，这里再兜一次给截图用）
        line = window.cmb_port.lineEdit()
        if line is not None:
            line.setCursorPosition(0)
        pump(app, 0.4)
        window.grab().save(os.path.join(OUT, "ble-mode.png"))

        # ---- 设置面板 ----
        window.cmb_link.setCurrentText("串口")
        pump(app, 0.2)
        # 车辆一节要看到「模组 1/2/3」的逐模组行，先造一台示例车并选中
        # （脚本收尾会按 snapshot 整体还原，不会留在用户配置里）
        import customization
        customization.save_vehicle("主包-2026", [25, 24, 25], [8, 5, 6])
        customization.select_vehicle("主包-2026")
        dialog = SettingsDialog(theme_mode="dark")
        try:
            dialog.show()
            pump(app, 0.6)
            dialog.grab().save(os.path.join(OUT, "settings.png"))
            # 再滚到「车辆（电池箱）」那一节拍一张：README 讲多车型时用它
            scroll = dialog.findChild(QScrollArea)
            combo = getattr(dialog, "cmb_vehicle_number", None)
            if scroll is not None and combo is not None:
                scroll.ensureWidgetVisible(combo, 0, 40)
                pump(app, 0.4)
                dialog.grab().save(os.path.join(OUT, "vehicle-settings.png"))
        finally:
            dialog.close()
        pump(app, 0.2)
    finally:
        window.setGeometry(0, 0, 1134, 820)
        window.close()
        app.processEvents()
        restore_settings(settings, snapshot)

    for name in ("main-window.png", "main-light.png", "data-coloring.png",
                 "ble-mode.png", "settings.png", "vehicle-settings.png"):
        path = os.path.join(OUT, name)
        print(f"  {name:22s} {os.path.getsize(path) // 1024} KB")
    print(f"→ {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
