# -*- coding: utf-8 -*-
"""heartbeatDLC 独立运行入口（不依赖主程序）。

    python -m heartbeatDLC                 打开心率面板
    python -m heartbeatDLC --scan 8       只扫描 8 秒，列出带 0x180D 的设备
    python -m heartbeatDLC --selftest     无硬件协议自测
    python -m heartbeatDLC --check        检查运行依赖
"""

from __future__ import annotations

import sys


def _check() -> int:
    from importlib.metadata import PackageNotFoundError, version

    for package in ("PySide6", "bleak"):
        try:
            print(f"{package} {version(package)}")
        except PackageNotFoundError:
            print(f"{package} 未安装 —— 请执行 pip install {package}")
            return 1
    return 0


def _scan(seconds: float) -> int:
    from PySide6.QtCore import QCoreApplication, QTimer
    from .hr_worker import HeartRateScanner

    app = QCoreApplication([])
    rows: list[dict] = []
    worker = HeartRateScanner(seconds)

    def on_devices(found) -> None:
        nonlocal rows
        rows = list(found or [])

    def on_failed(message: str) -> None:
        print(message, file=sys.stderr)

    worker.devices.connect(on_devices)
    worker.failed.connect(on_failed)
    worker.finished.connect(app.quit)
    QTimer.singleShot(int(seconds * 1000) + 5000, app.quit)
    worker.open()
    app.exec()
    worker.wait(500)
    if not rows:
        print("没扫到带 0x180D 的设备：确认手环已开启心率广播、手机已断开连接")
        return 1
    for info in rows:
        print(info.get("label") or info.get("address"))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in args:
        from .selftest import run
        return run()
    if "--check" in args:
        return _check()
    if "--scan" in args:
        index = args.index("--scan")
        seconds = 6.0
        if index + 1 < len(args):
            try:
                seconds = float(args[index + 1])
            except ValueError:
                pass
        return _scan(seconds)

    from PySide6.QtWidgets import QApplication
    from .hr_panel import HeartbeatPanel

    app = QApplication.instance() or QApplication(sys.argv)
    panel = HeartbeatPanel()
    panel.show()
    if QApplication.instance() is not app:        # 宿主已有事件循环时不接管
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
