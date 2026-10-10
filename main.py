"""串口调试助手 —— 程序入口。"""

from __future__ import annotations

import sys

# 软件版本号（发布 / 备份以它为准，git 上同时打同名 tag）。
__version__ = "1.0"

from PySide6.QtWidgets import QApplication

import customization
from main_window import MainWindow
from ui_brand import app_icon
from ui_theme import apply_scheme, detect_system_scheme, ui_font


def main() -> int:
    # 日志最先初始化：后面任何一步（含窗口构造）的异常都能留痕
    import app_log
    app_log.init()
    app_log.info("=" * 8 + f" 软件启动 v{__version__} " + "=" * 8)

    app = QApplication(sys.argv)
    app.setApplicationName("CQUFSAE串口调试助手")
    app.setOrganizationName("MimoWorks")
    # 窗口与任务栏图标。这一步不能省：--icon 只设了 exe 文件自身的图标，
    # 运行时的窗口图标要在这里显式给。
    app.setWindowIcon(app_icon())
    # 用 Fusion 打底：Windows 原生样式对自定义样式表的响应有不少怪脾气，
    # Fusion 更可预期，配色也由我们自己完全接管。
    app.setStyle("Fusion")
    # 先定主题再建窗口，窗口构造时拿到的就是正确配色，不会闪一下再换
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))

    window = MainWindow()
    window.show()
    # 起手姿态由「定制功能 → 启动时最大化」决定：默认开 = **整个窗口**全屏
    # （铺满显示器 + 盖住任务栏），关掉则先以普通窗口打开。页头/配置/发送区
    # 照常显示；"接收区展开"是另一件事，用户自己点放大按钮控制。
    window.apply_startup_state()
    app_log.info("软件就绪（起手姿态：%s）"
                 % ("整窗全屏" if customization.get("startup_maximized") else "普通窗口"))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
