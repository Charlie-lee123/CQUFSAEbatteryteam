# -*- coding: utf-8 -*-
"""「显示 SOC 读数」开关的端到端验收：设置面板 → 确定 → 底栏真的隐藏/恢复。

只读探针：不改产品代码。QSettings 全量快照 + finally 还原（⛔ 别删）。
用法：QT_QPA_PLATFORM=windows .venv\\Scripts\\python.exe devtools\\_verify_soc_switch.py
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings                      # noqa: E402
from PySide6.QtWidgets import QApplication                # noqa: E402

import customization                                      # noqa: E402
import rx_history                                         # noqa: E402

_fails = []
_total = [0]


def check(name, ok, detail=""):
    _total[0] += 1
    print(("[PASS] " if ok else "[FAIL] ") + name + ("  —— " + str(detail) if detail else ""))
    if not ok:
        _fails.append(name)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    tmp_path = ROOT / "_check" / "soc_switch"
    if tmp_path.exists():
        shutil.rmtree(tmp_path, ignore_errors=True)
    tmp_path.mkdir(parents=True, exist_ok=True)

    real_dir = rx_history._resolve_dir
    real_pin = customization.current_vehicle
    # ⛔ QSettings 快照：win.show()/close() 会走 _save_settings 写真实注册表
    qsettings = QSettings("MimoWorks", "SerialAssistant")
    snap = {key: qsettings.value(key) for key in qsettings.allKeys()}

    rx_history._resolve_dir = lambda: Path(tmp_path)
    customization.current_vehicle = lambda: "E74-2026"
    dlg = None
    win = None
    try:
        import main_window
        from ui_controls import Switch
        from ui_settings import SettingsDialog

        win = main_window.MainWindow()
        win.resize(1100, 720)
        win.show()
        app.processEvents()
        bar = win._bottom_bar

        check("S1 默认开着：四枚读数都可见",
              all(lbl.isVisibleTo(bar) for lbl in win._soc_labels),
              str([lbl.isVisibleTo(bar) for lbl in win._soc_labels]))

        dlg = SettingsDialog("dark", 55, (), (), on_custom=win._preview_custom)
        toggle = dlg._custom_widgets.get("soc_show")
        check("S2 设置面板按注册表生成了这个开关",
              isinstance(toggle, Switch),
              f"{type(toggle).__name__} keys={list(dlg._custom_widgets)[-3:]}")
        check("S3 开关初值 = 当前配置（默认开）",
              toggle is not None and toggle.isChecked(),
              str(None if toggle is None else toggle.isChecked()))

        toggle.setChecked(False)
        changes = dlg.custom_values()
        check("S4 拨下后 custom_values 报告 soc_show=False",
              changes.get("soc_show") is False, str(changes))

        # 模拟按「确定」：主窗口在关闭那一刻把结果抓走，动画结束再应用
        win._settings_values = dlg.values()
        win._settings_custom = changes
        win._settings_hidden = dlg.hidden_buttons()
        win._apply_settings_result(True)
        app.processEvents()

        check("S5 确定后底栏读数立刻隐藏（不用重启）",
              all(not lbl.isVisibleTo(bar) for lbl in win._soc_labels),
              str([lbl.isVisibleTo(bar) for lbl in win._soc_labels]))
        check("S6 隐藏后交还占位：零宽 + 提示不省略",
              all(lbl.width() == 0 for lbl in win._soc_labels)
              and win._toast_width_limit() == 0,
              f"w={[lbl.width() for lbl in win._soc_labels]}")
        check("S7 配置真的落盘了 soc_show=False",
              customization.get("soc_show") is False,
              str(customization.get("soc_show")))
        check("S8 关掉不影响估算链路（估算器与读数都还在）",
              win._soc_est is not None and win._soc_feed is not None, "")

        # 再打开
        win._settings_custom = {"soc_show": True}
        win._apply_settings_result(True)
        app.processEvents()
        shown = [lbl for lbl in win._soc_labels if lbl.text()]
        check("S9 重新打开后立即恢复可见",
              all(lbl.isVisibleTo(bar) for lbl in win._soc_labels)
              and len(shown) >= 1,
              f"visible={[lbl.isVisibleTo(bar) for lbl in win._soc_labels]}")
        if shown:
            left = min(lbl.x() for lbl in shown)
            right = max(lbl.x() + lbl.width() for lbl in shown)
            off = (left + right) // 2 - bar.width() // 2
            check("S10 恢复后仍居中（偏差 ≤ 4px）", abs(off) <= 4,
                  f"{off}px")
        check("S11 配置回到 soc_show=True",
              bool(customization.get("soc_show")), str(customization.get("soc_show")))
    finally:
        rx_history._resolve_dir = real_dir
        customization.current_vehicle = real_pin
        # ⛔ 还原用户配置：本文件新增的键删掉，其余按快照写回（geometry 尤其关键）
        live = QSettings("MimoWorks", "SerialAssistant")
        for key in live.allKeys():
            if key not in snap:
                live.remove(key)
        for key, value in snap.items():
            live.setValue(key, value)
        live.sync()
        try:
            if dlg is not None:
                dlg.deleteLater()
            if win is not None:
                # ⛔ 只 hide，不 close：close 会走 closeEvent→_save_settings，
                #    还会让主窗析构后仍挂着主题过渡的 0ms 单发，退出时抛
                #    "Internal C++ object already deleted"（退出码变 127）。
                #    注册表已按快照还原，这里不需要它再存一次。
                win.hide()
        except Exception:                                  # noqa: BLE001
            pass
        app.processEvents()

    print("\n%d/%d 项通过" % (_total[0] - len(_fails), _total[0]))
    print("IS_PASS:", "YES" if not _fails else "NO " + str(_fails))
    return 0 if not _fails else 1


if __name__ == "__main__":
    sys.exit(main())
