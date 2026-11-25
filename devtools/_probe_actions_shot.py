# -*- coding: utf-8 -*-
r"""一次性真平台截图探针：串口配置卡底部「数据工具」动作行区域 grab()。

用法：python devtools/_probe_actions_shot.py <outfile.png> [hover]
  - 展开配置卡（show 卡 + 放开内部滚动区），grab 卡片内容区。
  - hover：对 "查看标记" 这颗按钮触发 hover 补间到 1.0，再 grab（证明悬停浅底+文字变色）。
产物按 dpr 抓，存到指定路径。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from PySide6.QtCore import QPoint, QPointF
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication

import customization
import main_window
import rx_history


def main() -> int:
    outfile = sys.argv[1] if len(sys.argv) > 1 else "_check/ui_actions_after.png"
    do_hover = len(sys.argv) > 2 and sys.argv[2] == "hover"
    app = QApplication.instance() or QApplication(sys.argv)
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp(prefix="shot_", dir=Path(_ROOT) / "_check"))
    real_dir = rx_history._resolve_dir
    real_pin = customization.current_vehicle
    win = None
    try:
        rx_history._resolve_dir = lambda: tmp
        customization.current_vehicle = lambda: "E74-2026"
        win = main_window.MainWindow()
        win.resize(1400, 900)
        win.show()
        app.processEvents()
        app.processEvents()

        # 确保配置卡显示并展开（接收区默认展开态会 hide 配置卡）
        win._config_card.show()
        win._config_content.show()
        app.processEvents()
        app.processEvents()

        target = win._config_content
        if do_hover:
            btn = getattr(win, "btn_marks", None)
            if btn is not None:
                pos = QPointF(btn.width() / 2, btn.height() / 2)
                ev = QEnterEvent(pos, pos, win.mapToGlobal(QPoint(0, 0)))
                QApplication.sendEvent(btn, ev)
                app.processEvents()
                # 停掉 hover 补间并强制拉满：否则 processEvents 期间补间会把 _hover
                # 拉回中间值，抓到的浅底只有半透明（实测 #0e0f10 而非 surface_2）。
                btn._hover_anim.stop()
                btn._hover = 1.0
                btn.repaint()
                app.processEvents()
                target = win._config_content
                # 抓按钮所在卡片（含按钮）
                target = win._config_content

        pm = target.grab()
        out = Path(_ROOT) / outfile
        out.parent.mkdir(parents=True, exist_ok=True)
        pm.save(str(out))
        print(f"saved {out} ({pm.width()}x{pm.height()})")
    finally:
        if win is not None:
            win.close()
            win.deleteLater()
        app.processEvents()
        rx_history._resolve_dir = real_dir
        customization.current_vehicle = real_pin
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
