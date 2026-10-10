# -*- coding: utf-8 -*-
"""渲染检查：亮/暗主题主界面、毛玻璃强度、磨砂颗粒、接收区搜索条。

用真实 windows 平台插件渲染（不是 offscreen），所以出来的是实际观感。
产物全部落在 _check/ 下，供人工目视确认。
"""
import os
import sys

# 脚本挪进 devtools/ 之后，运行时项目根不再自动在 sys.path 里 —— 补上
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint, QRect, QSettings, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QToolTip

from main_window import BUTTON_SPECS, MainWindow
from ui_settings import SettingsDialog
from ui_theme import DEFAULT_GLASS, glass_strength, set_glass_strength

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_check")

# 搜索演示用的假日志：故意让关键词出现多次，好看清"当前命中"和"其它命中"的分层
DEMO_LINES = [
    "OK  boot ok, fw=1.4.2",
    "ADC ch0=1023 ch1=0512 ch2=0331",
    "CAN id=0x120 dlc=8 11 22 33 44 55 66 77 88",
    "WARN vbat=11.2V low",
    "ADC ch0=1019 ch1=0508 ch2=0330",
    "ERR timeout on CAN id=0x120",
    "ADC ch0=1015 ch1=0503 ch2=0328",
    "OK  loop 128 frames",
]


def pump(app, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)


def toggle_strip(app, window, out_path, scheme="light", frames=8, spacing=0.012):
    """把开关的通断过程按**等时间间隔**采样，纵向拼成一条序列图。

    等时间间隔采样、位移却不均匀 —— 这正是"非线性缓动"肉眼可见的样子：
    中间那几帧滑块跨得大（快），首尾几帧几乎没动（慢）。开关滑块是高对比、
    小位移的元素，用它演示缓动比用整块面板清楚得多。

    用 ``chk_hex_view`` 而不是 ``chk_timer_send``：后者勾上会去检查串口、
    弹一个模态提示，脚本会被卡住。
    """
    window._apply_theme(scheme)
    switch = window.chk_hex_view
    switch.setChecked(False)
    pump(app, 0.4)

    top_left = switch.mapTo(window, QPoint(0, 0))
    band = QRect(top_left.x() - 3, top_left.y() - 4,
                 switch.width() + 6, switch.height() + 8)

    shots = []
    switch.setChecked(True)
    for _ in range(frames):
        pump(app, spacing)
        shots.append(window.grab(band).toImage())
    pump(app, 0.5)

    line = 2
    step = band.height() + line
    strip = QImage(band.width(), step * len(shots),
                   QImage.Format.Format_ARGB32_Premultiplied)
    strip.fill(Qt.GlobalColor.white)
    painter = QPainter(strip)
    for index, image in enumerate(shots):
        y = index * step
        painter.drawImage(0, y, image)
        painter.setPen(QColor(196, 202, 210))
        painter.drawLine(0, y + band.height(), band.width(), y + band.height())
    painter.end()
    strip.save(out_path)

    switch.setChecked(False)
    pump(app, 0.4)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    app = QApplication(sys.argv)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    orig_glass = glass_strength()
    # 窗口尺寸会从注册表恢复（上一次真实运行留下的），而下面一堆图是按坐标裁的 ——
    # 不把尺寸钉死，裁出来的就是一片空白（实测被 exe 存下的 1600×930 坑过一次）。
    # 快照里有它，退出时原样还回去。
    settings.remove("geometry")
    try:
        window = MainWindow()
        # 别让插拔自动扫描在这台机器上真去开一个串口：截图要的是固定画面，
        # 而且脚本会反复换主题/隐藏控件，串口开着反而会写状态行。
        window._port_watch.stop()
        window.setGeometry(-4000, -4000, 1180, 940)
        window.show()
        pump(app, 1.2)

        for scheme in ("light", "dark"):
            window._apply_theme(scheme)
            pump(app, 0.3)
            window.grab().save(os.path.join(OUT, f"layout_{scheme}.png"))

        # ---- 毛玻璃强度三档（浅色）----
        window._apply_theme("light")
        for strength in (0, 50, 100):
            window._apply_glass(strength)
            pump(app, 0.3)
            window.grab(QRect(0, 150, 1180, 640)).save(
                os.path.join(OUT, f"glass_{strength}.png"))

        # 颗粒只在强度 > 0 时才铺，而默认档现在是 0（不要磨砂）—— 这张取样图
        # 要看的是颗粒本身，所以固定用最强档，别跟着默认值一起变成一张空图。
        window._apply_glass(100)
        pump(app, 0.3)
        # ---- 1:1 物理像素：横跨"纯白页面 -> 磨砂卡片"的边界，看颗粒本身 ----
        image = window.grab().toImage()
        image.copy(int(660 * 2), int(170 * 2), int(300 * 2), int(180 * 2)).save(
            os.path.join(OUT, "zoom_frost_1to1.png"))

        # ---- 串口配置栏：新增的「保留行数」，以及右上角的实时时钟 ----
        window._apply_theme("light")
        pump(app, 0.4)
        window.grab(QRect(870, 120, 320, 470)).save(os.path.join(OUT, "config_lines_light.png"))
        # 配置卡顶部：「串口开关」文字 + 电源按钮（它已搬进卡片，不再挂页头）
        window.grab(QRect(860, 190, 330, 90)).save(os.path.join(OUT, "header_light.png"))
        window.grab(QRect(560, 165, 340, 62)).save(os.path.join(OUT, "rx_clock_light.png"))
        # 发送区：初始只剩一排中文（数字框的左边距也一并入镜）。
        # 宽度给到整卡，右边那枚发送键的竖直位置要能一眼看出来 —— 它现在应该
        # 和第一行文字齐平，而不是沉在底下那排开关里。
        # ⚠️ 发送区**开局是折叠的**（用户点名"默认折叠起来"），这张要看的是
        # 展开态的版式，所以先展开再拍 —— 不展开的话这里拍到的是那条收起来的
        # 标题栏，输入框根本不在画面里（等于白拍）。上面 layout_*.png 那两张
        # 是全窗图，保留折叠态，正好当"默认长什么样"的记录。
        if window._tx_collapsed:
            window._toggle_send_area()
            pump(app, 0.3)
        window.grab(QRect(0, 630, 1180, 270)).save(os.path.join(OUT, "tx_oneline_light.png"))

        # ---- 接收区搜索 ----
        window.chk_timestamp.setChecked(False)
        # 走 _emit_block 而不是直接 appendPlainText：这是"一次接收"的真实入口，
        # 复制图标要靠它记账。绕过去的话日志有内容、图标却查不到那一条。
        for line in DEMO_LINES:
            window._emit_block(line)
        pump(app, 0.3)
        bar_crop = QRect(20, 255, 820, 150)

        for scheme in ("light", "dark"):
            window._apply_theme(scheme)
            pump(app, 0.3)
            window._open_search()
            # 展开动画（200ms）中途抓一张：证明它是连续长出来的，不是一帧跳变
            pump(app, 0.07)
            window.grab(bar_crop).save(os.path.join(OUT, f"search_reveal_mid_{scheme}.png"))
            pump(app, 0.4)
            window.edit_search.setText("ADC")
            window._search_timer.stop()
            window._refresh_matches()
            pump(app, 0.45)
            window.grab(bar_crop).save(os.path.join(OUT, f"search_hit_{scheme}.png"))
            window.grab().save(os.path.join(OUT, f"search_full_{scheme}.png"))

            window._find_next(True)
            pump(app, 0.25)
            window.grab(bar_crop).save(os.path.join(OUT, f"search_next_{scheme}.png"))

            window.edit_search.setText("zzz-not-here")
            window._search_timer.stop()
            window._refresh_matches()
            pump(app, 0.3)
            window.grab(bar_crop).save(os.path.join(OUT, f"search_miss_{scheme}.png"))
            window._close_search()
            pump(app, 0.4)

        # ---- 时间戳（轮首、秒级）与序号染蓝：喂真实数据格式，看实际观感 ----
        window._clear_receive()
        window.chk_timestamp.setChecked(True)
        demo_frames = [
            "Voltage1:\n" + "  ".join(f"{j}.38{20 + j % 7}" for j in range(1, 13))
            + "\n " + "  ".join(f"{j}.38{20 + j % 7}" for j in range(13, 26)),
            "Voltage2:\n" + "  ".join(f"{j}.38{21 + j % 5}" for j in range(1, 26)),
            "Temperature1:\n 1.22   2.22   3.22   4.25\n"
            "CWMax=3831,  Min=0\nV=0.00, I=-250.00",
        ]
        for frame in demo_frames:
            window._on_received(frame.encode("utf-8"))
            window._flush_rx()
        pump(app, 0.3)
        rx_crop = QRect(20, 165, 830, 320)
        for scheme in ("light", "dark"):
            window._apply_theme(scheme)
            pump(app, 0.3)
            window.grab(rx_crop).save(os.path.join(OUT, f"rx_highlight_{scheme}.png"))
        window.chk_timestamp.setChecked(False)
        window._clear_receive()
        pump(app, 0.2)

        # ---- 复制全部按钮：悬停高亮 + 箭头光标（手动点亮 hover 抓图）----
        window._apply_theme("light")
        window.btn_copy_all._hover = 1.0
        window.btn_copy_all.update()
        pump(app, 0.3)
        btn = window.btn_copy_all
        rect = QRect(btn.mapTo(window, QPoint(-8, -8)).x(),
                     btn.mapTo(window, QPoint(-8, -8)).y(), 46, 46)
        window.grab(rect).scaled(184, 184).save(os.path.join(OUT, "copy_all_hover.png"))
        print("copy_all cursor:", btn.cursor().shape())
        window.btn_copy_all._hover = 0.0
        window.btn_copy_all.update()
        pump(app, 0.2)

        # ---- 动画：等时间间隔采样，看非线性缓动 ----
        toggle_strip(app, window, os.path.join(OUT, "toggle_strip_light.png"))

        # ---- 设置面板：含「界面按钮」分组 ----
        for scheme in ("light", "dark"):
            window._apply_theme(scheme)
            pump(app, 0.3)
            dialog = SettingsDialog("system", DEFAULT_GLASS, BUTTON_SPECS, ["chk_hex_view"],
                                    parent=window)
            dialog.setGeometry(-4000, -4000, 520, dialog.sizeHint().height())
            dialog.show()
            pump(app, 0.6)
            dialog.grab().save(os.path.join(OUT, f"settings_buttons_{scheme}.png"))
            dialog.close()
            pump(app, 0.2)

        # ---- 悬停提示的字号（顺手读回真实字号，不靠肉眼估）----
        for scheme in ("light", "dark"):
            window._apply_theme(scheme)
            pump(app, 0.25)
            QToolTip.showText(QPoint(-3900, -3900), "暂停滚动（空格）", window)
            pump(app, 0.7)
            for widget in QApplication.topLevelWidgets():
                if (widget.isVisible()
                        and widget.windowType() == Qt.WindowType.ToolTip):
                    font = widget.font()
                    print(f"tooltip {scheme}: pt={font.pointSizeF()} "
                          f"px={font.pixelSize()}")
                    widget.grab().save(os.path.join(OUT, f"tooltip_{scheme}.png"))
            QToolTip.hideText()
            pump(app, 0.2)

        # ---- 带文字的控件被隐藏后的回流（含「编码」「历史」两个整行）----
        window._apply_theme("light")
        window._preview_buttons([])
        pump(app, 0.35)
        window.grab(QRect(20, 130, 1010, 260)).save(
            os.path.join(OUT, "buttons_all_shown_light.png"))
        window._preview_buttons(["chk_hex_view", "chk_append_crlf",
                                 "encoding_box", "history_box"])
        pump(app, 0.45)
        print("hidden rows:", sorted(window._hidden_buttons),
              "编码行可见:", window.encoding_box.isVisible(),
              "历史行可见:", window.history_box.isVisible())
        window.grab().save(os.path.join(OUT, "buttons_partial_light.png"))
        window._preview_buttons([key for key, _label, _group in BUTTON_SPECS])
        pump(app, 0.5)
        window.grab().save(os.path.join(OUT, "buttons_all_hidden_light.png"))
        window._preview_buttons([])
        pump(app, 0.35)

        # ---- 底栏左下角的齿轮：任何状态下都不铺底色 ----
        # 悬停是手动点亮的（抓图时鼠标不在窗口上，靠 enterEvent 永远点不亮）。
        # 抓的是底栏左端那一截 —— 有底色的话会浮出一块显眼的圆角方块。
        window.lbl_toast.setText("未发现串口")
        for scheme in ("light", "dark"):
            window._apply_theme(scheme)
            window.btn_settings._hover = 1.0
            window.btn_settings.update()
            pump(app, 0.3)
            shot = window.grab().toImage()
            shot.copy(QRect(0, shot.height() - 72, 620, 60)).scaled(620 * 2, 60 * 2).save(
                os.path.join(OUT, f"bottom_bar_{scheme}.png"))
            window.btn_settings._hover = 0.0
            window.btn_settings.update()
            pump(app, 0.15)
        window._apply_theme("light")
        pump(app, 0.2)

        window.close()
    finally:
        set_glass_strength(orig_glass)
        for key in list(settings.allKeys()):
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()
    print("shots ->", OUT)


if __name__ == "__main__":
    main()
