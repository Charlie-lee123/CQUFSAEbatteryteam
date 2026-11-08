"""探针：真实 Win32 ShowWindow(SW_MINIMIZE)→ShowWindow(SW_RESTORE) 复现
「最大化→最小化→从任务栏还原」后窗口的真实状态与几何，定位四周缝隙根因。

重点看：还原后 isFullScreen/isMaximized/windowState、窗口 geometry 相对屏幕几何的
四周间隙（GEOMETRIC GAP）、以及 card_pad（padding 是否收 0）。
"""
import os
import sys
import time
import ctypes

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings
from main_window import MainWindow


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    # 防污染：进程序前快照 QSettings，跑完还原
    gs = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: gs.value(k) for k in gs.allKeys()}

    w = MainWindow()
    w._build_ui()
    w._want_fullscreen = True
    w.showFullScreen()
    w._enforce_native_fullscreen()
    app.processEvents()
    time.sleep(0.3)

    hwnd = int(w.winId())
    SW_MINIMIZE, SW_RESTORE = 6, 9

    def dump(tag: str) -> None:
        sg = w.screen().geometry()
        g = w.geometry()
        print(f"[{tag}] isFullScreen={w.isFullScreen()} isMaximized={w.isMaximized()} "
              f"state={w.windowState()} card_pad={w._card_pad} "
              f"want_fs={w._want_fullscreen}")
        print(f"    geom={g.x()},{g.y()},{g.width()}x{g.height()}  screen={sg.width()}x{sg.height()}")
        print(f"    GAP L={g.x()-sg.x()} T={g.y()-sg.y()} "
              f"R={sg.right()-g.right()} B={sg.bottom()-g.bottom()}")

    print("=== AFTER FULLSCREEN ===")
    dump("full")

    ctypes.windll.user32.ShowWindow(hwnd, SW_MINIMIZE)
    for _ in range(10):
        app.processEvents(); time.sleep(0.03)
    time.sleep(0.3)
    print("=== AFTER MINIMIZE ===")
    dump("min")

    ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
    # 泵事件 ~700ms，让 changeEvent 里 80/220/260ms 的 singleShot 全部落地
    for _ in range(70):
        app.processEvents(); time.sleep(0.01)
    time.sleep(0.5)
    print("=== AFTER RESTORE (from taskbar) ===")
    dump("restore")

    # 还原 QSettings，防污染用户配置
    for k in gs.allKeys():
        if k not in snap:
            gs.remove(k)
    for k, v in snap.items():
        gs.setValue(k, v)
    gs.sync()
    w.close(); w.deleteLater(); app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
