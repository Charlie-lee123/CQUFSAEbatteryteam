# -*- coding: utf-8 -*-
"""DLC 挂载探针：真实构造主窗口，验证插件入口按钮与面板能起来。

    .venv/Scripts/python.exe devtools/_probe_dlc_mount.py

⛔ 用 offscreen 平台 + ``SERIALASSISTANT_DLC_DIR`` 隔离；进窗前对 QSettings
   取快照，结束在 finally 里原样还原，绝不污染用户真实配置。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / "_check" / "dlc_stage"
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["SERIALASSISTANT_DLC_DIR"] = str(STAGE)
sys.path.insert(0, str(ROOT))

import dlc_host  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

RESULTS: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    RESULTS.append((name, bool(ok)))


def main() -> int:
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    app = None
    window = None
    try:
        package = ROOT / "发布" / "heartbeatDLC-1.0.0.zip"
        if not package.is_file():
            print("缺少发布包，请先运行 devtools/_build_heartbeat_dlc.py")
            return 1
        dlc_host.install_zip(package)

        from main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        # QTimer.singleShot(0, _init_dlc) 要等一拍事件循环才跑
        for _ in range(20):
            app.processEvents()
        check("主窗口带「扩展 / DLC」按钮", hasattr(window, "btn_dlc"))
        check("插件已被加载", "heartbeatDLC" in window._dlc_loaded)
        # offscreen 下主窗没 show()，isVisible() 对所有子控件恒为 False；
        # 要看的是「有没有被显式隐藏」，用 isVisibleTo(祖先)。
        check("插件入口容器已显式显示",
              window._dlc_launchers.isVisibleTo(window))
        check("插件注册了 1 颗入口按钮", len(window._dlc_buttons) == 1)
        check("入口按钮文字正确",
              bool(window._dlc_buttons)
              and window._dlc_buttons[0].text() == "心率 DLC")

        plugin = window._dlc_loaded.get("heartbeatDLC")
        if plugin is not None:
            plugin.show_panel()
            app.processEvents()
            panel = plugin._panel
            check("心率面板构造成功", panel is not None)
            check("面板标题正确", panel is not None
                  and "heartbeatDLC" in panel.windowTitle())
            check("初始只有「全部设备」标签", panel.tabs.count() == 1)
            check("初始无设备视图", not panel._details and not panel._cards)
            check("面板未启动 BLE 线程",
                  panel._scanner is None and panel._hub is None)

            # 不碰蓝牙，直接灌两台不同设备的样本，验证多设备分别成视图、数据不串
            first = "AA:BB:CC:DD:EE:01"
            second = "AA:BB:CC:DD:EE:02"
            panel._on_devices([
                {"address": first, "name": "Xiaomi Smart Band 10 Pro",
                 "rssi": -55, "xiaomi": True, "hr_declared": True,
                 "label": "小米 10 Pro"},
                {"address": second, "name": "Polar H10",
                 "rssi": -70, "xiaomi": False, "hr_declared": True,
                 "label": "Polar H10"},
            ])
            for address, bpm, name in ((first, 72, "10 Pro"),
                                       (second, 88, "Polar H10")):
                panel._on_sample({
                    "bpm": bpm, "contact": None, "energy": None, "rr_ms": (),
                    "flags": 0, "ts": 0, "address": address, "name": name})
            app.processEvents()
            check("两台设备各自建详情", len(panel._details) == 2)
            check("总览 + 两台详情共 3 个标签", panel.tabs.count() == 3)
            check("设备 A 的心率互不串扰",
                  panel._details[first].lbl_bpm.text() == "72")
            check("设备 B 的心率互不串扰",
                  panel._details[second].lbl_bpm.text() == "88")
            check("总览卡片分别显示",
                  panel._cards[first].lbl_bpm.text() == "72"
                  and panel._cards[second].lbl_bpm.text() == "88")
            check("各设备独立趋势与统计",
                  len(panel._details[first].samples) == 1
                  and len(panel._details[second].samples) == 1
                  and panel._details[first].trend is not panel._details[second].trend)
            plugin.shutdown()
            app.processEvents()
            check("shutdown 后面板引用已释放", plugin._panel is None)

        window._shutdown_dlc()
        check("退出收尾后插件表清空", not window._dlc_loaded)
    finally:
        # ⛔ 无论上面哪一步抛错，都要把用户设置还原回去
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            if value is None:
                settings.remove(key)
            else:
                settings.setValue(key, value)
        settings.sync()
        # _shutdown_dlc() 已调用且幂等，析构不会再做一次收尾
        if app is not None:
            app.processEvents()
        try:
            current = next((i for i in dlc_host.discover()
                            if i.id == "heartbeatDLC"), None)
            if current is not None:
                dlc_host.uninstall(current)
        except (OSError, ValueError):
            pass

    for name, ok in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    failed = [name for name, ok in RESULTS if not ok]
    print(f"\nDLC 挂载探针：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
