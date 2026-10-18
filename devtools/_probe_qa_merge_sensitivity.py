# -*- coding: utf-8 -*-
"""临时实验：用 QA 自己的取样器量退出段，只改"合并同一视觉帧"的阈值。

目的：证明 QA 报的 G/退出 13.3px/ms 是**取样口径**造成的，不是真的跳——
接收区补间被 min_interval_ms 节流到 ≈60fps（16ms 才更新一次几何），而
GeometryTap 按显示器 165Hz（6ms）取一次样：同一次几何更新被 2~3 个采样点
分摊，落在 6ms 上就放大 16/6 ≈ 2.7 倍。

用法：QT_QPA_PLATFORM=windows python devtools/_dbg_merge_probe.py
"""
from __future__ import annotations

import importlib.util
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings                     # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

spec = importlib.util.spec_from_file_location(
    "qa", os.path.join(_ROOT, "devtools", "_qa_anim_verdict.py"))
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)

app = QApplication(sys.argv)
app.setStyle("Fusion")
from ui_theme import apply_scheme, detect_system_scheme, ui_font   # noqa: E402
apply_scheme(detect_system_scheme())
app.setFont(ui_font(12.5))
from ui_anim import display_max_hz, set_frame_hz                  # noqa: E402
set_frame_hz(display_max_hz())

settings = QSettings("MimoWorks", "SerialAssistant")
snap = {k: settings.value(k) for k in settings.allKeys()}

from main_window import MainWindow                        # noqa: E402
w = MainWindow()
w._port_watch.stop()
w.setGeometry(0, 0, 1180, 860)
w.show()
w.apply_startup_state()
qa.pump(app, 1.2)

with qa.GeometryTap(app, w) as tap:
    for label, action in (("展开", w._expand_receive), ("退出", w._collapse_receive)):
        if label == "退出" and not w._expanded:
            tap.run(w._expand_receive)
            qa.pump(app, 0.8)
        rows = tap.run(action)
        qa.pump(app, 0.6)
        span = (rows[-1][0] - rows[0][0]) * 1000 if len(rows) > 1 else 0
        print(f"\n=== {label}：{len(rows)} 次 tick 取样 / {span:.0f}ms ===")
        for merge_ms in (3.5, 8.0, 12.0, 16.0):
            qa._merge_subframes.__defaults__ = (merge_ms,)
            merged = qa._merge_subframes(rows)
            print(f"  -- 合并阈值 {merge_ms:>4.1f}ms → {len(merged)} 帧")
            for short, ok, detail, _r in qa.analyse_geometry(rows):
                print(f"     {'PASS' if ok else 'FAIL'} {short:<8} {detail[:78]}")

try:
    if w._expanded:
        w._collapse_receive()
        qa.pump(app, 1.0)
except Exception:                              # noqa: BLE001
    pass
try:
    if w.isFullScreen():
        w.showNormal()
except RuntimeError:
    pass
w.setGeometry(0, 0, 1134, 820)
w.close()
for k in set(settings.allKeys()) - set(snap):
    settings.remove(k)
for k, v in snap.items():
    if v is not None:
        settings.setValue(k, v)
settings.sync()
