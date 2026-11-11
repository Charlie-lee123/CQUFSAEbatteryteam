# -*- coding: utf-8 -*-
"""heartbeatDLC —— 心率广播 DLC（可选插件）。

宿主通过 ``manifest.json`` 的 ``entry`` 找到本包，调用 ``create(ctx)``；
插件只依赖宿主上下文提供的三件东西：注册入口按钮、数据目录、版本号。
**不修改宿主的 QSettings、不改全局 QSS、不接管串口/蓝牙主通道。**
"""

from __future__ import annotations

PLUGIN_ID = "heartbeatDLC"
PLUGIN_NAME = "心率广播 DLC"
PLUGIN_VERSION = "1.0.0"


class HeartbeatDLC:
    """插件实例：持有一个非模态面板，关闭主程序时由宿主调 ``shutdown()``。"""

    def __init__(self, ctx) -> None:
        self._ctx = ctx
        self._panel = None
        ctx.log(f"{PLUGIN_NAME} v{PLUGIN_VERSION} 已加载（{PLUGIN_ID}）")
        ctx.add_launcher(
            "心率 DLC", self.show_panel,
            tooltip="打开心率广播 DLC：连接小米手环 8 代及以后的 BLE 心率服务")

    def show_panel(self) -> None:
        from .hr_panel import HeartbeatPanel

        if self._panel is None:
            self._panel = HeartbeatPanel(self._ctx)
        self._panel.show()
        self._panel.raise_()
        self._panel.activateWindow()

    def shutdown(self) -> None:
        if self._panel is not None:
            self._panel.shutdown()
            self._panel = None


def create(ctx):
    """宿主插件接口（DLC API v1）：返回插件对象。"""
    return HeartbeatDLC(ctx)
