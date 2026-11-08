# -*- coding: utf-8 -*-
r"""定位「第一个下拉框构造」的一次性成本（od53 帧率排查第三步）。

背景
----
`_probe_splash_gap.py` 修完字体/图标/快照三处后，最大堵点仍是 **~267ms**，
栈顶落在 `main_window.py:1535 self.cmb_bytesize = ChevronComboBox()` →
`ui_controls.py:1019 currentTextChanged.connect(...)`。栈顶只比真实位置晚一行，
真凶是它前面那次 **C++ 调用**（`QComboBox.__init__` 或首次 QSS polish）。

本探针把启动状态复刻出来（Fusion + apply_scheme + ui_font + 先把字体引擎
在离屏图上走通一遍，等价于开屏首帧），然后**逐个**计时：
  - `QComboBox()`（裸，无 parent）
  - `QComboBox(parent)`（有 parent）
  - `ChevronComboBox()` / `ChevronComboBox(parent)`
  - 首次 `setEditable(True)` / `addItems` / `setCurrentText`
用来判断这笔钱到底是「QComboBox 类首次构造」还是「带 parent 的 polish」。

⛔ 只读探针：不改产品源码、不打包、不写 QSettings。
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROWS: list[tuple[str, float]] = []


def mark(name: str, t0: float) -> float:
    ms = (time.perf_counter() - t0) * 1000.0
    ROWS.append((name, ms))
    print(f"  {ms:8.1f} ms  {name}", flush=True)
    return ms


def main() -> int:
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication, QComboBox, QWidget

    import ui_theme

    _snap = {}
    _st = QSettings("MimoWorks", "SerialAssistant")
    _snap = {k: _st.value(k) for k in _st.allKeys()}

    t = time.perf_counter()
    app = QApplication.instance() or QApplication([sys.argv[0]])
    mark("QApplication()", t)

    t = time.perf_counter()
    app.setStyle("Fusion")
    mark('setStyle("Fusion")', t)

    t = time.perf_counter()
    mode = str(QSettings("MimoWorks", "SerialAssistant").value(
        "theme_mode", "system") or "system")
    scheme = ui_theme.detect_system_scheme() if mode == "system" else mode
    ui_theme.apply_scheme(scheme)
    mark("apply_scheme（app.setStyleSheet）", t)

    t = time.perf_counter()
    app.setFont(ui_theme.ui_font(12.5))
    mark("app.setFont(ui_font(12.5))", t)

    # 等价开屏首帧：字体引擎 + 光栅化路径先在离屏图上走通
    t = time.perf_counter()
    img = QImage(320, 80, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setFont(ui_theme.ui_font(12.5))
    p.drawText(img.rect(), 0x0084, "115200 波特率")
    p.end()
    mark("离屏 drawText（≈开屏首帧，字体引擎暖机）", t)

    parent = QWidget()
    parent.resize(400, 300)

    t = time.perf_counter()
    c1 = QComboBox()
    mark("QComboBox()  #1（无 parent）", t)
    t = time.perf_counter()
    c1b = QComboBox()
    mark("QComboBox()  #2（无 parent）", t)

    import ui_controls

    t = time.perf_counter()
    c2 = ui_controls.ChevronComboBox(parent)
    mark("ChevronComboBox(parent)  #1", t)
    t = time.perf_counter()
    c3 = ui_controls.ChevronComboBox(parent)
    mark("ChevronComboBox(parent)  #2", t)

    t = time.perf_counter()
    c2.setEditable(True)
    mark("chevron.setEditable(True) 首次", t)
    t = time.perf_counter()
    c2.addItems(["9600", "19200", "115200"])
    mark("chevron.addItems(3 项) 首次", t)
    t = time.perf_counter()
    c2.setCurrentText("115200")
    mark("chevron.setCurrentText() 首次", t)
    t = time.perf_counter()
    c2.setFont(ui_theme.num_font(11.5))
    mark("chevron.setFont(num_font(11.5))", t)
    t = time.perf_counter()
    c2.grab()
    mark("chevron.grab() 首次", t)
    t = time.perf_counter()
    c2.grab()
    mark("chevron.grab() 第二次", t)

    for w in (c1, c1b, c2, c3, parent):
        w.deleteLater()

    # QSettings 原样还原
    st2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st2.allKeys()) - set(_snap):
        st2.remove(k)
    for k, v in _snap.items():
        st2.setValue(k, v)
    st2.sync()

    print("\n=== 结论 ===")
    top = sorted(ROWS, key=lambda kv: -kv[1])[:5]
    for name, ms in top:
        print(f"  {ms:8.1f} ms  {name}")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:                      # noqa: BLE001 - 探针自证
        import traceback
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    os._exit(code)
