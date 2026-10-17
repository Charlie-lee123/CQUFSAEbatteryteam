# -*- coding: utf-8 -*-
r"""动效演示：把这一轮改过的动效**逐个真跑一遍**给用户看（约 50 秒）。

不是截图，是真人可见的操作序列：窗口摆到屏幕正中，真实移动鼠标做悬停，
程序化触发那些必须点进去才看得到的动效（下拉展开 / 搜索条展开 / 蓝牙扫描的
矢量弧 / 连接后的数据脉冲），每步之间留足观看时间，并在终端同步打出旁白。

⚠️ 结束时把窗口几何搬回屏幕内再关：main_window 的 closeEvent 会把当前几何写进
   注册表，直接关会把演示用的坐标永久写进用户配置（踩过）。

用法：
    python devtools\_demo_motion.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

import ctypes                                            # noqa: E402
import ctypes.wintypes as wt                             # noqa: E402

from PySide6.QtCore import QPoint, QSettings      # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

user32 = ctypes.windll.user32

# 演示里用得到的循环端口：pyserial 自带的假串口，不需要任何硬件
LOOP_PORT = "loop://demo"


def narrate(text: str) -> None:
    print(f"  ▸ {text}", flush=True)


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.008)


def glide_to(app, widget, steps: int = 14, duration: float = 0.35,
             offset: QPoint | None = None) -> None:
    """把鼠标**平滑地**挪到控件上（悬停动效要真鼠标，不能靠合成事件）。"""
    target = widget.mapToGlobal(widget.rect().center()) + (offset or QPoint(0, 0))
    point = wt.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    start = (point.x, point.y)
    for i in range(1, steps + 1):
        k = i / steps
        x = int(start[0] + (target.x() - start[0]) * k)
        y = int(start[1] + (target.y() - start[1]) * k)
        user32.SetCursorPos(x, y)
        app.processEvents()
        time.sleep(duration / steps)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    saved_geom = settings.value("geometry")

    import customization
    from ui_theme import apply_scheme, ui_font
    from main_window import LINK_BLE, LINK_SERIAL, MainWindow

    for spec in customization.specs():
        customization.set(spec["key"], spec["default"])
    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    window = MainWindow()
    window._port_watch.stop()
    # 摆到屏幕正中，别用屏幕外那套（现在是给人看的）
    screen = app.primaryScreen().availableGeometry()
    w, h = min(1180, screen.width() - 80), min(900, screen.height() - 80)
    window.setGeometry(screen.x() + (screen.width() - w) // 2,
                       screen.y() + (screen.height() - h) // 2, w, h)
    window.show()
    window.raise_()
    window.activateWindow()
    pump(app, 1.0)
    narrate("窗口就位（深色主题、默认全屏起手）")

    try:
        # ① 悬停：按钮底色淡入（150ms out_cubic —— 手一碰就要有反应）
        narrate("① 鼠标滑过左下角齿轮：悬停底色淡入")
        glide_to(app, window.btn_settings)
        pump(app, 1.2)
        narrate("   再滑到右上角「打开历史数据」")
        glide_to(app, window.btn_open_history)
        pump(app, 1.1)

        # ② 下拉弹层：150ms 从锚点展开 + 新条目样式（圆角内嵌高亮）
        narrate("② 展开「校验位」下拉：弹层 150ms 展开动画")
        window.cmb_parity.showPopup()
        pump(app, 0.9)
        narrate("   鼠标在选项上滑过：悬停是内缩圆角色块（这条是这次的修复）")
        view = window.cmb_parity.view()
        for i in (1, 2, 3):
            rect = view.visualRect(view.model().index(i, 0))
            global_pos = view.viewport().mapToGlobal(rect.center())
            user32.SetCursorPos(global_pos.x(), global_pos.y())
            pump(app, 0.55)
        window.cmb_parity.hidePopup()
        pump(app, 0.5)

        # ②b 这次用户点名的那个：编码下拉（原先里面还套一层框，现已修掉）
        narrate("②b 再开「编码」下拉（你截图说难看的那个）：现在只有一层干净面板")
        window.cmb_encoding.showPopup()
        pump(app, 0.9)
        v2 = window.cmb_encoding.view()
        for i in (2, 1, 0):
            rect = v2.visualRect(v2.model().index(i, 0))
            gp = v2.viewport().mapToGlobal(rect.center())
            user32.SetCursorPos(gp.x(), gp.y())
            pump(app, 0.5)
        window.cmb_encoding.hidePopup()
        pump(app, 0.5)

        # ③ 搜索条：220ms in_out_quint 展开（高度 + 透明度一起走）
        narrate("③ Ctrl+F 展开搜索条：220ms 两头慢的连续展开")
        window._toggle_search()
        pump(app, 1.4)
        narrate("   收起")
        window._close_search()
        pump(app, 0.8)

        # ④ 矢量弧：切到蓝牙广播 → 真扫 3.5 秒，状态灯的弧在转
        narrate("④ 连接方式切「蓝牙广播」：状态灯右侧的弧开始转（3.5 秒扫描）")
        window.cmb_link.setCurrentText(LINK_BLE)
        pump(app, 1.2)
        narrate("   看底栏状态灯 —— 匀速旋转 + 弧长非线性的呼吸")
        pump(app, 2.6)
        narrate("   扫描收尾：弧停、设备列表填入")
        pump(app, 1.2)
        window.cmb_link.setCurrentText(LINK_SERIAL)
        pump(app, 0.9)

        # ⑤ 真连一个 loop:// 假串口：看开关滑块 + 数据脉冲 + 接收区着色
        narrate("⑤ 接一个不需要硬件的 loop:// 假串口，拨开关")
        window._select_port(LOOP_PORT)
        pump(app, 0.4)
        window.btn_open.click()                  # 滑块 190ms out_quint 滑过去
        pump(app, 1.6)
        narrate("   发一行 BMS 数据：接收区着色 + 状态灯脉冲余晖")
        window._on_received(
            b"[2026-10-15 22:30:00]\n"
            b"01.3820 02.3824 03.3860 04.4990 05.2100 06.3720 07.3750 08.3780\n"
            b"Temperature1:\n01.220 02.950 03.910 04.240\nBMS ERROR\n")
        pump(app, 1.8)

        narrate("⑥ 定位最近异常：跳转 + 整行高亮")
        window.btn_alert.click()
        pump(app, 1.6)

        narrate("⑦ 接收区最大化：页头→配置→发送 依次让位，接收区连续长高")
        window._expand_receive()
        pump(app, 2.2)
        narrate("   收起：发送→配置→页头 依次归位（严格镜像）")
        window._collapse_receive()
        pump(app, 2.2)

        narrate("   断开")
        window.btn_open.click()
        pump(app, 1.0)
        narrate("演示结束 ✔（窗口留在这里，可以直接点着玩）")
        pump(app, 1.5)
    finally:
        # 关窗**前**把几何搬回屏幕内：closeEvent 会把当前几何写进注册表
        if saved_geom is not None:
            window.setGeometry(saved_geom)
        window.close()
        for key, value in snapshot.items():
            if value is not None:
                settings.setValue(key, value)
        settings.sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
