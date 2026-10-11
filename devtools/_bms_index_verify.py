# -*- coding: utf-8 -*-
"""BMS ERROR 明细行序号不补零的验证（2026-10-11 用户点名）。

造一轮真实形态的数据：电压 25 点 + 温度 8 点 + BMS ERROR + 21 条错误明细，
喂进主窗口后截图，肉眼确认明细行序号保持 "1.1" 原样（不是 "01.1"）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 真机 windows 平台跑（offscreen 中文渲染方框、字体度量退化，见项目记忆）

from PySide6.QtWidgets import QApplication

import main_window as mw
import customization

app = QApplication(sys.argv)

settings = mw.QSettings("MimoWorks", "SerialAssistant")
snapshot = {k: settings.value(k) for k in settings.allKeys()}
try:
    customization.set("startup_maximized", False)
    window = mw.MainWindow()
    window.show()
    app.processEvents()

    volt = " ".join(f"{j}.6553" for j in range(1, 26))
    temp = " ".join(f"{j}.87" for j in range(1, 9))
    detail = "\n".join(f"{j}.1 Over Voltage" for j in range(1, 22))
    window._on_received(
        f"[2026-10-11 16:20:00]\nVoltage1:\n{volt}\n"
        f"Temperature1:\n{temp}\nBMS ERROR\n{detail}\n".encode("utf-8"))
    # 合并缓冲（30ms 静默窗）+ 半行缓发要靠事件循环吐数据，转 500ms 等它出
    import time
    end = time.time() + 0.5
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()

    window.resize(1180, 900)
    app.processEvents()
    text = window.txt_rx.toPlainText()
    assert "1.1 Over Voltage" in text and "01.1" not in text, text[:400]

    out = os.path.join("_check", "bms_index_verify.png")
    window.grab().save(out)
    print("saved:", out)
finally:
    for k, v in snapshot.items():
        if v is None:
            settings.remove(k)   # ⚠️ 别写 "v is None and s.remove(k) or ..."：
        else:                    #    remove 返回 None 会掉进 setValue(k, None)，
            settings.setValue(k, v)  #    键删不掉还写进脏值（2026-10-11 踩过）
