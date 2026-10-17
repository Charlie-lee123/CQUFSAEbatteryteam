# -*- coding: utf-8 -*-
"""判定 selftest 三条挂项：接收区那行电压值是被**按模组重排拆行**（功能，设计如此），
还是**真的丢了字节**（产品 bug）。

喂同一段原始报文，把渲染后的文档全文打出来，逐个核对样本值是否都在、且只出现一次。
无论用户在不在（自定义模组数/每模组电芯数），结论都必须成立。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

RAW = ("Voltage1:\n1.3700   2.3820   3.3900\n"
       "Temperature1:\n1.22   2.95   3.45\n"
       "BMS ERROR\n1.1 Over Voltage\n")
VALUES = ["1.3700", "2.3820", "3.3900", "1.22", "2.95", "3.45"]


def pump(app, ms):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.004)


def dump(window, app, tag):
    window._clear_receive()
    window._on_received(RAW.encode("utf-8"))
    window._flush_rx()
    app.processEvents()
    doc = window.txt_rx.document()
    rows = [doc.findBlockByNumber(b).text() for b in range(doc.blockCount())]
    print(f"\n[{tag}] 渲染后 {len(rows)} 行：")
    for i, t in enumerate(rows):
        print(f"  {i:2d}| {t}")
    joined = "\n".join(rows)
    missing = [v for v in VALUES if v not in joined]
    dup = [v for v in VALUES if joined.count(v) > 1]
    print(f"  缺失值: {missing or '无'}")
    print(f"  重复值: {dup or '无'}")
    # 每个样本值所在行
    for v in VALUES:
        where = [i for i, t in enumerate(rows) if v in t]
        print(f"  {v} -> 行 {where}")
    return missing, dup


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    settings = QSettings("MimoWorks", "SerialAssistant")
    keys = ("custom/module_count", "custom/cells_per_module",
            "custom/temps_per_module", "custom/module_count_chosen",
            "custom/cells_per_module_chosen", "custom/temps_per_module_chosen",
            "custom/volt_limit_low", "custom/volt_limit_high")
    snapshot = {k: settings.value(k) for k in keys}
    try:
        from ui_theme import apply_scheme, ui_font
        from selftest import make_window
        import customization

        app.setFont(ui_font(12.5))
        apply_scheme("dark")

        print("=== 你的真实配置 ===")
        for k in keys[:3]:
            print(f"  {k} = {settings.value(k)}")

        window = make_window()
        window.show()
        pump(app, 300)
        print("\n窗口报告的网格参数:",
              "module_count=", customization.get("module_count"),
              "cells_per_module=", customization.get("cells_per_module"),
              "temps_per_module=", customization.get("temps_per_module"))
        miss_a, dup_a = dump(window, app, "真实配置")
        window.close()
        pump(app, 100)

        # 再用默认（0=自动）跑一遍对照
        for k in ("custom/module_count", "custom/cells_per_module",
                  "custom/temps_per_module"):
            settings.setValue(k, 0)
        for k in ("custom/module_count_chosen", "custom/cells_per_module_chosen",
                  "custom/temps_per_module_chosen"):
            settings.remove(k)
        settings.sync()
        window2 = make_window()
        window2.show()
        pump(app, 300)
        print("\n对照（全部默认 0=自动）:")
        print("  module_count=", customization.get("module_count"),
              "cells_per_module=", customization.get("cells_per_module"),
              "temps_per_module=", customization.get("temps_per_module"))
        miss_b, dup_b = dump(window2, app, "默认配置")
        window2.close()
        pump(app, 100)

        print("\n================ 判定 ================")
        print(f"真实配置下缺失={miss_a or '无'} 重复={dup_a or '无'}")
        print(f"默认配置下缺失={miss_b or '无'} 重复={dup_b or '无'}")
        if not miss_a and not miss_b:
            print("-> 两边都没有字节丢失：差异只是**按模组换行**（设计功能）")
        else:
            print("-> 有值丢失，属于产品 bug，需修")
        return 0
    finally:
        for k, v in snapshot.items():
            if v is None:
                settings.remove(k)
            else:
                settings.setValue(k, v)
        settings.sync()


if __name__ == "__main__":
    sys.exit(main())
