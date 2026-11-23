# -*- coding: utf-8 -*-
r"""读串口期间「保持电脑不熄屏」—— 纯逻辑，无 Qt，可脱离界面单测。

为什么单独一个模块
------------------
Windows 的 ``SetThreadExecutionState`` 是**每线程**状态：必须 acquire / release
在同一线程（GUI 主线程）调用，否则不生效。这里把「开关 + 串口是否在收 + 是否已
acquire」三态收敛成一处幂等状态机：只有 ``want = enabled and receiving`` 与
``held`` 不一致时才真的调 OS，避免每帧重复设置。

⛔ 不做定时器轮询：状态只在「设置开关改 / 串口开成功 / 串口关闭 / 退出」四个
离散事件翻。异常退出由 OS 自动回收线程执行状态，不会永久改电源策略。

注入点
------
``os_setter(flags:int)`` 默认指向 ``ctypes.windll.kernel32.SetThreadExecutionState``；
selftest 注入 fake 断言 acquire/release 次数与 flag，**绝不真改测试机电源策略**。
非 Windows（``sys.platform != "win32"``）整段降级为 no-op。
"""
from __future__ import annotations

import ctypes
import sys

#: Windows 线程执行状态标志（见 MSDN SetThreadExecutionState）
ES_CONTINUOUS = 0x80000000
ES_DISPLAY_REQUIRED = 0x1
ES_SYSTEM_REQUIRED = 0x2
#: acquire = 持续 + 要求显示器开 + 要求系统开；release = 仅持续（恢复默认）
ACQUIRE_FLAGS = ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED
RELEASE_FLAGS = ES_CONTINUOUS


def default_os_setter(flags: int) -> int | None:
    """真实 OS 调用：非 Windows no-op；Windows 声明 argtypes/restype 后调用。

    失败（DLL 取不到 / 信号被拒）一律吞掉 —— 防息屏是附赠功能，接收是主功能。
    """
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        setter = kernel32.SetThreadExecutionState
        setter.argtypes = [ctypes.wintypes.DWORD]
        setter.restype = ctypes.wintypes.DWORD
        return setter(flags)
    except Exception:                    # noqa: BLE001 - OS 调用失败不能影响主流程
        return None


class WakeLock:
    """防息屏状态机。``enabled``（设置开关）/ ``receiving``（串口在收）/ ``held``（已 acquire）。"""

    def __init__(self, os_setter=None) -> None:
        self.enabled: bool = False
        self.receiving: bool = False
        self.held: bool = False
        # 注入点：显式传入则用之，否则每次调用时取模块默认（便于 selftest 打桩）
        self._os_setter = os_setter

    def _call_os(self, flags: int) -> None:
        setter = self._os_setter if self._os_setter is not None else default_os_setter
        setter(flags)

    def _acquire(self, os_setter=None) -> None:
        """.acquire：阻止息屏/睡眠。幂等由 :meth:`_sync` 保证，这里只管调一次。"""
        setter = os_setter if os_setter is not None else self._os_setter
        if setter is not None:
            setter(ACQUIRE_FLAGS)
        else:
            self._call_os(ACQUIRE_FLAGS)
        self.held = True

    def _release(self, os_setter=None) -> None:
        """release：恢复系统默认电源策略。"""
        setter = os_setter if os_setter is not None else self._os_setter
        if setter is not None:
            setter(RELEASE_FLAGS)
        else:
            self._call_os(RELEASE_FLAGS)
        self.held = False

    def _sync(self) -> None:
        """唯一状态翻转出口：``want = enabled and receiving``，仅 want != held 时调 OS。"""
        want = bool(self.enabled and self.receiving)
        if want == self.held:
            return
        if want:
            self._acquire()
        else:
            self._release()

    def configure(self, enabled: bool) -> None:
        """设置开关变化（来自 customization.get("keep_awake_on_rx")）。"""
        self.enabled = bool(enabled)
        self._sync()

    def set_receiving(self, active: bool) -> None:
        """串口接收状态变化（开成功 True / 关闭或打开失败 False）。"""
        self.receiving = bool(active)
        self._sync()

    def release(self) -> None:
        """强制恢复默认（退出时调用）：即使 receiving 仍为真也 release。"""
        self.receiving = False
        if self.held:
            self._release()
