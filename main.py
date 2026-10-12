"""串口调试助手 —— 程序入口。"""

from __future__ import annotations

import sys

# 软件版本号（发布 / 备份以它为准，git 上同时打同名 tag）。
# 唯一出处是 version.py：main 一被 import 就会拉起 MainWindow，
# 其它模块（如设置面板）要从 version.py 取，不能反向 import 这里。
from version import VERSION as __version__

from PySide6.QtWidgets import QApplication

import customization
from main_window import MainWindow
from ui_brand import app_icon
from ui_theme import apply_scheme, detect_system_scheme, ui_font


def _run_apply_update(target: str) -> int:
    """`--apply-update <目标exe>`：自动更新的最后一步。

    旧实例已把新 exe 下载到临时目录并以本参数拉起**我们**（新 exe）；旧实例
    随即退出。这里等目标 exe 的文件锁释放（onefile 的引导进程退出要几秒），
    然后用自己覆盖目标、重启新版本。配置全在注册表，替换 exe 不丢任何设置。
    """
    import os
    import shutil
    import subprocess
    import time

    deadline = time.time() + 60
    while True:
        try:
            shutil.copyfile(sys.executable, target)
            break
        except PermissionError:
            if time.time() > deadline:
                app_log.error(f"自动更新失败：目标文件始终被占用 {target}")
                return 1
            time.sleep(0.5)
    subprocess.Popen([target], cwd=os.path.dirname(target) or ".",
                     close_fds=True)
    return 0


def main() -> int:
    # 自动更新的自举分支：不做任何 GUI，覆盖完目标就退出
    target = None
    if "--apply-update" in sys.argv:
        from updater import parse_apply_update_args
        target = parse_apply_update_args(sys.argv)
    if target:
        import app_log
        app_log.init()
        app_log.info(f"自动更新：接管替换流程，目标 {target}")
        return _run_apply_update(target)

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
