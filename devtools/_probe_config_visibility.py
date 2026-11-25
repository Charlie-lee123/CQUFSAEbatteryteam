# -*- coding: utf-8 -*-
r"""一次性真平台探针：串口设置卡纳入「界面按钮」显隐 + DLC 只在蓝牙模式显示。

证明（写 _check/config_visibility_probe.txt）：
  A BUTTON_SPECS 新增三个 key（serial_params / rx_lines_box / actions_box）、
    分组「串口设置」、属性都能在主窗上取到；设置面板里三个开关都在
  B 隐藏「串口参数」→ 4 行（波特率/停止位/数据位/校验位）一起消失
  C 隐藏「数据工具」→ 整组（含分隔线+标题+5 行按钮）消失
  D 隐藏「保留行数」→ 标签+下拉框一起消失
  E 三者互不影响（藏一个另两个仍在）
  F link_mode 切蓝牙 → DLC 可见；切串口 → 不可见
  G 隐藏「串口参数」且切到蓝牙广播时仍不显示（组合正确）；DLC 不受 serial_params 影响
  H QSettings 前后零变化
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from PySide6.QtCore import QEvent, QSettings
from PySide6.QtWidgets import QApplication

import customization
import main_window
import rx_history

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def main() -> int:
    import shutil
    import tempfile
    from pathlib import Path

    app = QApplication.instance() or QApplication(sys.argv)
    qs = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: qs.value(k) for k in qs.allKeys()}
    real_dir = rx_history._resolve_dir
    real_pin = customization.current_vehicle
    tmp = Path(tempfile.mkdtemp(prefix="cfgvis_", dir=Path(_ROOT) / "_check"))
    win = None
    try:
        rx_history._resolve_dir = lambda: tmp
        customization.current_vehicle = lambda: "E74-2026"
        win = main_window.MainWindow()
        win.resize(1400, 900)
        # 这台机器默认是蓝牙广播模式，serial_params 按设计整体隐藏；本探针 A/B/E
        # 的「串口参数可见」前提必须钉串口（与 selftest.make_window 同口径）。
        win.cmb_link.setCurrentText(main_window.LINK_SERIAL)
        win.show()
        win._config_card.show()
        win._config_content.show()
        app.processEvents()

        # ---- A BUTTON_SPECS 三个新 key ----
        specs = dict((k, (lbl, grp)) for k, lbl, grp in main_window.BUTTON_SPECS)
        for key in ("serial_params", "rx_lines_box", "actions_box"):
            check(f"A1 {key} 在 BUTTON_SPECS 里", key in specs)
            check(f"A2 {key} 分组=串口设置", specs.get(key, ("", ""))[1] == "串口设置",
                  str(specs.get(key)))
            check(f"A3 {key} 属性可取到", getattr(win, key, None) is not None)
        check("A4 「串口开关」仍不在清单里",
              "btn_open" not in specs and "cmb_link" not in specs)
        check("A5 DLC 仍不在清单里", "btn_dlc" not in specs)
        # 标签文案
        check("A6 标签分别为 串口参数/保留行数/数据工具",
              specs["serial_params"][0] == "串口参数"
              and specs["rx_lines_box"][0] == "保留行数"
              and specs["actions_box"][0] == "数据工具",
              str({k: v[0] for k, v in specs.items() if k in
                   ("serial_params", "rx_lines_box", "actions_box")}))

        # 设置面板里三个开关都在
        from ui_settings import SettingsDialog
        dlg = SettingsDialog("dark", 50,
                              main_window.BUTTON_SPECS, (),
                              on_glass=None, on_buttons=None)
        app.processEvents()
        sw = getattr(dlg, "_button_switches", {})
        check("A7 设置面板列出三个新开关",
              all(k in sw for k in ("serial_params", "rx_lines_box", "actions_box")),
              str(sorted(sw)))
        dlg.close()
        dlg.deleteLater()
        app.processEvents()

        # ---- B/C/D/E 隐藏各自生效且互不影响 ----
        rows = (win.cmb_baud, win.cmb_stopbits, win.cmb_bytesize, win.cmb_parity)

        def hidden_now() -> set[str]:
            out = set()
            for w in rows:
                out.add(w.objectName() or "")
            return out

        win._preview_buttons(["serial_params"])
        app.processEvents()
        check("B1 隐藏串口参数 → 整组不可见", win.serial_params.isHidden())
        # ⚠️ 子控件用 isVisible() 判：Qt 里 isHidden() 只反映"自己被显式隐藏"，
        #    父容器藏起来时子控件仍是 isHidden()==False，要靠 isVisible() 才看得出。
        check("B2 四行控件都跟着消失",
              all(not w.isVisible() for w in rows),
              str([w.isVisible() for w in rows]))
        check("B3 保留行数仍在", not win.rx_lines_box.isHidden())
        check("B4 数据工具仍在", not win.actions_box.isHidden())

        win._preview_buttons(["actions_box"])
        app.processEvents()
        check("C1 隐藏数据工具 → 整组不可见", win.actions_box.isHidden())
        check("C2 串口参数仍在（上一个已解除）", not win.serial_params.isHidden())
        check("C3 保留行数仍在", not win.rx_lines_box.isHidden())

        win._preview_buttons(["rx_lines_box"])
        app.processEvents()
        check("D1 隐藏保留行数 → 容器不可见", win.rx_lines_box.isHidden())
        check("D2 标签+下拉框一起没了（容器整体收）",
              not win.cmb_rx_lines.isVisible())

        win._preview_buttons([])
        app.processEvents()
        check("E1 全部恢复：串口参数可见", not win.serial_params.isHidden())
        check("E2 全部恢复：保留行数可见", not win.rx_lines_box.isHidden())
        check("E3 全部恢复：数据工具可见", not win.actions_box.isHidden())

        # 互不影响：只藏一个时另两个仍可见
        win._preview_buttons(["serial_params"])
        app.processEvents()
        check("E4 只藏串口参数 → 另两组不受影响",
              not win.rx_lines_box.isHidden() and not win.actions_box.isHidden())
        win._preview_buttons(["rx_lines_box"])
        app.processEvents()
        check("E5 只藏保留行数 → 另两组不受影响",
              not win.serial_params.isHidden() and not win.actions_box.isHidden())
        win._preview_buttons([])
        app.processEvents()

        # ---- F link_mode 切蓝牙 → DLC 可见；切串口 → 不可见 ----
        from main_window import LINK_BLE, LINK_SERIAL
        win.cmb_link.setCurrentText(LINK_BLE)
        app.processEvents()
        check("F1 蓝牙广播模式 DLC 可见", win.btn_dlc.isVisible()
              and not win.btn_dlc.isHidden())
        check("F2 蓝牙广播模式串口参数整体隐藏", win.serial_params.isHidden())
        win.cmb_link.setCurrentText(LINK_SERIAL)
        app.processEvents()
        check("F3 串口模式 DLC 不可见", win.btn_dlc.isHidden())
        check("F4 串口模式串口参数可见", not win.serial_params.isHidden())

        # ---- G 组合：隐藏串口参数 + 切蓝牙 ----
        win._preview_buttons(["serial_params"])
        app.processEvents()
        win.cmb_link.setCurrentText(LINK_BLE)
        app.processEvents()
        check("G1 隐藏串口参数 + 蓝牙模式 → 仍不显示", win.serial_params.isHidden())
        check("G2 蓝牙模式下 DLC 仍可见（不受串口参数隐藏影响）",
              win.btn_dlc.isVisible())
        check("G3 数据工具组不受影响", not win.actions_box.isHidden())
        win.cmb_link.setCurrentText(LINK_SERIAL)
        app.processEvents()
        win._preview_buttons([])
        app.processEvents()

    finally:
        if win is not None:
            win.close()
            win.deleteLater()
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        rx_history._resolve_dir = real_dir
        customization.current_vehicle = real_pin
        for k in list(qs.allKeys()):
            if k not in snap:
                qs.remove(k)
        for k, v in snap.items():
            qs.setValue(k, v)
        qs.sync()
        shutil.rmtree(tmp, ignore_errors=True)

    now = {k: qs.value(k) for k in qs.allKeys()}
    check("H QSettings 前后零变化", now == snap, f"keys {len(snap)} -> {len(now)}")
    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    tail = f"\n合计 {passed}/{total} PASS  ->  IS_PASS: {'YES' if passed == total else 'NO'}"
    print(tail)
    with open(os.path.join(_ROOT, "_check", "config_visibility_probe.txt"),
              "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                           (f"   [{d}]" if d else "") for n, ok, d in _RESULTS)
                 + tail + "\n")
    app.quit()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
