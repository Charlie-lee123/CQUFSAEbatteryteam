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
        check("主窗口带「DLC」按钮", hasattr(window, "btn_dlc"))
        check("「DLC」按钮文案正确",
              hasattr(window, "btn_dlc") and window.btn_dlc.text() == "DLC")
        # 2026-11-12 用户口径：「DLC 按钮放串口设置那里」—— 必须是串口配置卡
        # 里的控件（跟着「打开历史数据」排），不再是接收区右侧功能列的一员。
        check("「DLC」按钮在串口配置卡内",
              hasattr(window, "btn_dlc")
              and window._config_card.isAncestorOf(window.btn_dlc))
        check("「DLC」按钮已离开接收区功能列",
              hasattr(window, "btn_dlc")
              and not window._rx_rail.isAncestorOf(window.btn_dlc))
        check("插件已被加载", "heartbeatDLC" in window._dlc_loaded)
        # 新设计（2026-11-12 用户口径）：界面只留一颗「DLC」按钮，
        # 不再动态生成插件入口按钮，插件只注册面板回调。
        check("界面没有多余的插件入口按钮",
              not hasattr(window, "_dlc_buttons")
              or not window._dlc_buttons)
        check("插件注册了 1 个面板回调", len(window._dlc_launcher_cbs) == 1)
        # ⛔ 用户 2026-11-12 明确要求：主界面**只留一颗「DLC」按钮**。
        # 遍历主窗全部控件，任何带「心率」字样的按钮/开关都算违规。
        from PySide6.QtWidgets import QLabel, QMessageBox, QWidget  # noqa: PLC0415
        _titled = [w for w in window.findChildren(QWidget)
                   if hasattr(w, "text") and callable(w.text)]
        _stray = []
        for w in _titled:
            try:
                text = str(w.text())
            except Exception:                   # noqa: BLE001 - 非文本控件直接跳过
                continue
            if "心率" in text and not isinstance(w, QLabel):
                _stray.append(f"{type(w).__name__}:{text}")
        check("界面没有任何「心率」按钮/开关", not _stray)
        if _stray:
            print("    违规控件：", _stray)

        # ---- 单 DLC 直通流程（用户 2026-11-12 口径）---------------------
        # ① 已安装 → 点「DLC」应当**直接**打开心率面板，不弹管理对话框
        _info = window._heartbeat_dlc()
        check("能识别到唯一 DLC", _info is not None and _info.id == "heartbeatDLC")
        check("随包安装包可被找到", window._find_dlc_package() is not None)
        window._on_dlc_clicked()
        app.processEvents()
        _plugin = window._dlc_loaded.get("heartbeatDLC")
        check("点「DLC」直接进入 DLC 面板",
              _plugin is not None and _plugin._panel is not None)
        check("DLC 面板内有「卸载」按钮",
              _plugin is not None and _plugin._panel is not None
              and _plugin._panel.btn_uninstall.text() == "卸载 DLC")

        plugin = _plugin
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

        # ---- ② 面板内「卸载」：宿主确认 → 停实例 → 删目录（先备份）---------
        import main_window as _mw  # noqa: PLC0415 - 探针内局部导入

        class _StubBox:
            """把模态框换成固定答案，避免探针在离屏下卡住。"""
            StandardButton = QMessageBox.StandardButton

            @staticmethod
            def question(*_a, **_k):
                return QMessageBox.StandardButton.Yes

            @staticmethod
            def information(*_a, **_k):
                return QMessageBox.StandardButton.Ok

            warning = information

        _orig_box = _mw.QMessageBox
        _mw.QMessageBox = _StubBox
        try:
            _installed_dir = (dlc_host.user_dir() / "heartbeatDLC")
            check("卸载前插件目录存在", _installed_dir.is_dir())
            check("面板内卸载成功返回 True",
                  window._dlc_request_uninstall("heartbeatDLC") is True)
            app.processEvents()
            check("卸载后插件目录已删除", not _installed_dir.exists())
            check("卸载后插件已从运行表移除",
                  "heartbeatDLC" not in window._dlc_loaded)
            check("卸载后已彻底发现不到", window._heartbeat_dlc() is None)

            # ③ 没装 → 点「DLC」走安装提示（这里回答 No，确认不会偷偷装上）
            class _NoBox(_StubBox):
                @staticmethod
                def question(*_a, **_k):
                    return QMessageBox.StandardButton.No

            _mw.QMessageBox = _NoBox
            window._on_dlc_clicked()
            app.processEvents()
            check("未安装时点「DLC」只提示不安装",
                  window._heartbeat_dlc() is None
                  and "heartbeatDLC" not in window._dlc_loaded)
        finally:
            _mw.QMessageBox = _orig_box

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
