# -*- coding: utf-8 -*-
"""探针：① 字号滑杆 + 预览 ② 导出日志按钮 ③ 窗口边缘缩放光标。

只读，可在主工作区安全运行（自带 QSettings 快照还原）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication


def move_to(app, widget, point: QPoint) -> None:
    """往 widget 的本地坐标 point 发一个 MouseMove（走应用级过滤器）。

    ⚠️ 这里**故意不调 processEvents**：那样会顺带处理真实的鼠标移动事件
    （探针跑起来时鼠标可能正停在窗口上），把刚设好的光标盖回去，量到的是
    假值。sendEvent 本身是同步的，投递完立刻读就够了。
    """
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(point),
                        QPointF(widget.mapToGlobal(point)),
                        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def main() -> None:
    import customization
    from main_window import MainWindow
    from ui_settings import SettingsDialog
    from ui_theme import qcolor

    app = QApplication.instance() or QApplication(sys.argv)
    import app_log
    app_log.init()          # 日志节在 path() 为 None 时整节跳过，先初始化
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    window = MainWindow()
    try:
        window.resize(1000, 760)
        window.showNormal()
        window.show()
        app.processEvents()

        # ---------- ③ 边缘缩放光标 ----------
        print("=== ③ 边缘缩放光标 ===")
        # ⚠️ 窗口最边缘那圈并不属于 txt_rx 的 viewport（它在卡片里、四周还有
        # 内边距），真机上鼠标在那儿时事件发生在**外层容器**上。所以这里拿
        # centralWidget 当事件目标（它的左上角就是窗口客户区原点）。
        target = window.centralWidget()
        origin = target.mapTo(window, QPoint(0, 0))
        mid_y = target.height() // 2
        print(f"事件目标 {type(target).__name__} 相对窗口原点 {origin.x()},{origin.y()}")

        # 先直接验证"边缘几何 → 光标形状"这张映射表（不经过事件路由）
        shapes = []
        for name, point in (("左", QPoint(2, mid_y)),
                            ("右", QPoint(window.width() - 3, mid_y)),
                            ("上", QPoint(target.width() // 2, 2)),
                            ("下", QPoint(target.width() // 2, window.height() - 3)),
                            ("左上", QPoint(2, 2)),
                            ("右下", QPoint(window.width() - 3, window.height() - 3)),
                            ("右上", QPoint(window.width() - 3, 2)),
                            ("左下", QPoint(2, window.height() - 3)),
                            ("中央", QPoint(target.width() // 2, mid_y))):
            edges = window._resize_edges(point)
            shape = window._edge_cursor_shape(edges)
            shapes.append((name, shape.name if shape else None))
        print("几何映射表:", shapes)

        def cursor_shape() -> str:
            return target.cursor().shape().name

        sample = (("左边缘", QPoint(2, mid_y)), ("上边缘", QPoint(target.width() // 2, 2)),
                  ("左上角", QPoint(2, 2)))
        for name, point in sample:
            move_to(app, target, point)
            print(f"{name} -> {cursor_shape()}  host={window._cursor_host}")
        print("saved_cursors:", list(window._saved_cursors))
        move_to(app, target, QPoint(target.width() // 2, mid_y))
        print("回到中央 ->", cursor_shape(), "（该控件原本的形状）")

        window.showFullScreen()
        app.processEvents()
        move_to(app, target, QPoint(2, mid_y))
        print("全屏时左边缘 ->", cursor_shape(), "（不该是缩放光标）")
        window.showNormal()
        app.processEvents()

        # ---------- ① 字号滑杆 + 预览 ----------
        print("\n=== ① 字号滑杆 + 实时预览 ===")
        seen: list = []
        dialog = SettingsDialog(
            "dark", 0, on_custom=lambda key, value: (
                seen.append((key, value)), window._preview_custom(key, value)))
        dialog.show()
        app.processEvents()
        spec = next(s for s in customization.specs() if s["key"] == "rx_font_size")
        slider = dialog._custom_widgets["rx_font_size"]
        preview = dialog._custom_previews["rx_font_size"]
        print(f"控件 {type(slider).__name__} 范围 [{slider.minimum()}, {slider.maximum()}] "
              f"scale={spec.get('scale')} 存储值={slider.value()}")
        print(f"预览文字={preview.text()!r} 字号={preview.font().pointSizeF()}")
        ink = qcolor("ink").name()
        print(f"预览颜色：styleSheet={preview.styleSheet()!r} 应含 ink={ink} -> "
              f"{ink in preview.styleSheet()}")
        before = window.txt_rx.font().pointSizeF()
        slider.setValue(slider.value() + 4)          # +2.0 pt
        app.processEvents()
        after = window.txt_rx.font().pointSizeF()
        print(f"拖到 {slider.value()}（+2pt）后：预览字号={preview.font().pointSizeF()} "
              f"接收区 {before} -> {after}  回调={seen}")
        slider.setValue(21)
        app.processEvents()
        print(f"还原 21 后：接收区字号={window.txt_rx.font().pointSizeF()} "
              f"预览字号={preview.font().pointSizeF()}")

        # ---------- ② 导出日志按钮 ----------
        print("\n=== ② 导出日志 ===")
        print(f"按钮存在: {dialog.btn_export_log.text()!r} 可用={dialog.btn_export_log.isEnabled()}")

        print(f"日志文件: {app_log.path()}")
        dialog.close()
    finally:
        window.close()
        app.processEvents()
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()


if __name__ == "__main__":
    main()
