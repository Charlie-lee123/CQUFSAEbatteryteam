# -*- coding: utf-8 -*-
r"""子进程测：不同字体族下「首次 QSS 控件文本测量」的耗时。

背景：cProfile 显示 MainWindow 构造 937ms 里 720ms 花在
`_clock_width` 的 QFontMetrics.horizontalAdvance 首次调用上（第 2 次 0ms）。
但单独 new QFont + horizontalAdvance 只要 0.3ms —— 差异在 QSS + 全局字体
+ 真实 QLabel 的组合。每个字体族必须在**独立子进程**里测，否则第一个会把
Qt 字体引擎热起来、后面全是 0ms（假快）。

用法：python devtools/_probe_font_first.py            # 主控：逐个起子进程
      python devtools/_probe_font_first.py <family>   # 子进程：单一字体
"""
import os
import subprocess
import sys

_ROOT = r"F:\MimoCodeworks\SerialAssistant"

if len(sys.argv) > 1:
    fam = sys.argv[1]
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    sys.path.insert(0, _ROOT)
    import time

    from PySide6.QtWidgets import QApplication, QLabel
    from PySide6.QtGui import QFont

    app = QApplication([])
    app.setStyle("Fusion")
    import ui_theme
    from ui_theme import apply_scheme

    # 用真实主题 QSS
    apply_scheme("dark")
    app.setFont(QFont(fam, int(12.5)) if fam != "default" else ui_theme.ui_font(12.5))
    # 真实控件：带 QSS 的 QLabel，量 19 字符占位符（和 _clock_width 同款）
    lbl = QLabel("")
    lbl.show()
    app.processEvents()
    t0 = time.perf_counter()
    w = lbl.fontMetrics().horizontalAdvance("0000-00-00 00:00:00")
    dt = (time.perf_counter() - t0) * 1000
    print(f"RESULT\t{fam}\t{dt:.1f}\t{w}")
    sys.exit(0)

FAMILIES = ["default", "Segoe UI Variable Display", "Segoe UI",
            "Microsoft YaHei UI", "Arial"]
print(f"{'字体族':>28} {'首次测量':>10}")
for fam in FAMILIES:
    out = subprocess.run(
        [sys.executable, os.path.abspath(__file__), fam],
        capture_output=True, text=True,
        env={**os.environ, "QT_QPA_PLATFORM": "windows"})
    line = next((ln for ln in out.stdout.splitlines()
                 if ln.startswith("RESULT")), None)
    if line:
        parts = line.split("\t")
        print(f"{parts[1]:>28} {float(parts[2]):>9.1f} ms  (宽 {parts[3]})")
    else:
        print(f"{fam:>28}   失败: {out.stderr[-200:]}")
