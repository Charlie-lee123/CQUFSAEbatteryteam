"""串口调试助手 —— 程序入口。"""

from __future__ import annotations

import os
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

    # ⚠️ 必须在**函数内**再 import 一次：``import app_log`` 写在 main() 里只绑到
    #    main 的局部名，这个模块级函数看不到它 —— 以前这里用的是全局名，真走到
    #    "目标文件被占用超时" 这条错误分支时会抛 NameError（本该报错的地方自己崩了）。
    import app_log

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


def _run_ble_scan(argv: list[str]) -> int:
    """`--ble-scan [秒数] [manufacturer|service] [key]`：不进界面，扫 BLE 广播。

    两个用处，都很实在：
      1. **验证产物**：打包后的 exe 里 bleak/WinRT 有没有真的打进去（BLE 这套
         依赖是二进制轮子，PyInstaller 漏掉就只能在真机上试才知道）；
      2. **给用户查设备**：把扫到的设备名 / 地址 / 广播负载十六进制打出来 ——
         "我的设备到底发没发、发的是什么" 一眼就能看出来，不用猜。

    只扫描、不连接、不配对（和软件里的蓝牙广播模式是同一条路）。
    """
    import time

    from PySide6.QtCore import QCoreApplication

    seconds, source, key = 6.0, "manufacturer", ""
    rest = [a for a in argv[1:] if not a.startswith("--")]
    if rest:
        try:
            seconds = max(1.0, float(rest[0]))
        except ValueError:
            pass
    if len(rest) > 1:
        source = rest[1]
    if len(rest) > 2:
        key = rest[2]

    # 只给 Qt 程序名：别让它去解析我们的命令行参数
    app = QCoreApplication(argv[:1])
    from ble_io import BleBroadcastWorker

    seen: dict[str, dict] = {}
    worker = BleBroadcastWorker("", source=source, key=key)
    worker.found.connect(lambda info: seen.update({info["address"]: info}))
    worker.open_failed.connect(lambda msg: print("扫描起不来:", msg))
    worker.failed.connect(lambda msg: print("扫描出错:", msg))
    print(f"扫描 BLE 广播 {seconds:.0f} 秒（source={source} key={key or '任意'}）...")
    worker.open()
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    worker.stop()

    rows = [info for info in seen.values() if info.get("payload")]
    lines = [f"扫描 BLE 广播 {seconds:.0f} 秒（source={source} key={key or '任意'}）",
             f"共发现 {len(seen)} 个 BLE 设备，其中 {len(rows)} 个带着广播负载："]
    for info in sorted(rows, key=lambda i: -i["rssi"])[:20]:
        lines.append(f"  {info['address']}  {info['name'] or '(无名)'}"
                     f"  RSSI {info['rssi']}")
        lines.append(f"      负载 = {info['payload'].hex()}")
    if not rows:
        lines.append("  （没有设备在广播里带数据。设备侧要主动把数据放进广播包，"
                     "光配对/连接是收不到的）")

    # ⚠️ 产物是 console=False 的窗口程序，stdout 接不到任何控制台 —— 只打印的话
    #    用户双击运行就什么都看不到。所以同时写一份到产物目录，并把路径说出来。
    #    （app_log 要先初始化才知道日志目录在哪；这一步在上面打印之前其实是空的。）
    import app_log

    app_log.init()
    text = "\n".join(lines)
    path = ""
    try:
        log_dir = os.path.dirname(app_log.path() or "")
        if log_dir:
            path = os.path.join(log_dir, "蓝牙扫描结果.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text + "\n")
    except OSError:
        path = ""
    print(text)
    if path:
        print(f"\n（结果也写到了：{path}）")
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

    # 命令行探针：只扫 BLE 广播、不进界面（验证产物 / 查自己的设备）
    if "--ble-scan" in sys.argv:
        return _run_ble_scan(sys.argv)

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
