"""探针：验证「全屏→最小化→从任务栏还原」后的四周缝隙修复。

根因：Qt 在 全屏→最小化→还原 链上会丢掉全屏标志，窗口以"普通态"回来
（isFullScreen/isMaximized 都 False），但用户仍想要全屏（_want_fullscreen 置真）。
修复后 _sync_fullscreen_padding 把 _want_fullscreen 也视作"铺满"，padding 直接收 0，
不会让卡片阴影 3px 被当成四周缝隙。

本探针在**普通（非全屏）窗口**上构造该状态，断言：
  · _want_fullscreen=True  → card_pad == 0 且 内容边距 == 0
  · _want_fullscreen=False → card_pad == CARD_LIFT_ROOM（普通态留缓冲，正常）
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

# 把项目根加进 import 路径（脚本在 devtools/ 下）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from main_window import MainWindow, CARD_LIFT_ROOM


def build_window() -> MainWindow:
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w._build_ui()
    # 普通窗口态（非全屏/非最大化），正是"还原后 Qt 丢标志"时的样子
    w.showNormal()
    app.processEvents()
    return w


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    w = build_window()

    # —— 场景 1：用户想要全屏（最大化进来的必经之路），但窗口此刻被 Qt 当成普通态
    w._want_fullscreen = True
    w._sync_fullscreen_padding()
    pad_want = w._card_pad
    m_left = w._inner_lay.contentsMargins().left()
    ok_want = (pad_want == 0) and (m_left == 0)

    # —— 场景 2：普通窗口（用户主动退出全屏），应保留 3px 缓冲
    w._want_fullscreen = False
    w._sync_fullscreen_padding()
    pad_normal = w._card_pad
    ok_normal = (pad_normal == CARD_LIFT_ROOM)

    w.close()
    w.deleteLater()
    app.processEvents()

    print(f"[probe] _want_fullscreen=True  → card_pad={pad_want} margin={m_left} "
          f"expect 0  => {'OK' if ok_want else 'FAIL'}")
    print(f"[probe] _want_fullscreen=False → card_pad={pad_normal} "
          f"expect {CARD_LIFT_ROOM} => {'OK' if ok_normal else 'FAIL'}")

    # 还原 QSettings 防污染（探针铁律）
    from PySide6.QtCore import QSettings
    gs = QSettings("MimoWorks", "SerialAssistant")
    gs.remove("custom/module_count")
    gs.remove("custom/cells_per_module")
    gs.remove("custom/temps_per_module")
    gs.sync()
    return 0 if (ok_want and ok_normal) else 1


if __name__ == "__main__":
    sys.exit(main())
