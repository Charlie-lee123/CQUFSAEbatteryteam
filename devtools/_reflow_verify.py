# -*- coding: utf-8 -*-
"""验证 2026-10-11 第二轮排版修复：半行缓发 + 折行按视口封顶。

按用户三张截图的真实劈裂位置造分包时序（电压 "13.6553" 孤格、温度 "1.87"
孤行、"Over Vol"/"tage" 腰斩），喂进真实窗口后截图。
产物：_check/reflow_verify2.png
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtWidgets import QApplication

from selftest import make_window

app = QApplication(sys.argv)
window = make_window()
window.show()
window.resize(1180, 900)
app.processEvents()
window.chk_timestamp.setChecked(True)
window._clear_receive()
window._apply_theme("light")
app.processEvents()


def pump(seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def feed(chunks):
    """按真实节奏喂分包：块间隔 60ms（超出 30ms 静默窗，逼出劈裂场景）。"""
    for chunk in chunks:
        window._on_received(chunk)
        pump(0.06)


V = " ".join(f"{j}.6553" for j in range(1, 13))
W = " ".join(f"{j}.6553" for j in range(14, 26))
feed([
    # 电压：一行从 12 个劈到 13 个（旧版会在 13 处甩孤格）
    f"Voltage4:\n{V}\n13.6553".encode("utf-8"),
    f"  {W}\n".encode("utf-8"),
    # 温度2：劈成 "1.87" / " 2.87 … 5.87" / "6.87 …" 三截
    b"Temperature2:\n1.87",
    b" 2.87 3.87 4.87 5.87\n",
    b"6.87 7.87 8.87\n",
    # 告警行："Over Vol" / "tage" 腰斩
    b"05.6 Over Vol",
    b"tage\n",
    b"05.7 Over Voltage\nBMS OK\n",
])
pump(0.5)          # 等半行缓发的尾巴吐出来

out = os.path.join(_ROOT, "_check", "reflow_verify2.png")
window.txt_rx.grab().save(out)
print("saved ->", out)
print(window.txt_rx.toPlainText())
window.close()
