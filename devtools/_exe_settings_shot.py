# -*- coding: utf-8 -*-
"""产物实测：打开设置面板并截图（补 _exe_check 的短板）。

为什么单独一支：``_exe_check`` 的「点设置按钮」依赖**真的把窗口抢到前台**，
而后台跑脚本时 Windows 的前台锁经常拒绝 ``SetForegroundWindow`` —— 点击就落到
了别的窗口上（实测 8 次偏移全失败，但同一坐标手动跑一次就成功）。
这里改成：抢前台 → 校验 ``GetForegroundWindow()`` 确实是它 → 再点；
抢不到就用 ``SetWindowPos(HWND_TOPMOST)`` 顶上来，保证点得到。

⛔ 不写用户的配置：临时摘掉 geometry / 关掉启动最大化，跑完原样还回去。
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "_check", "exe_settings_shot.png")
EXE = os.path.join(ROOT, "发布", "SerialAssistant.exe")

ctypes.windll.shcore.SetProcessDpiAwareness(2)
user32 = ctypes.windll.user32
SW_RESTORE = 9
HWND_TOPMOST = -1
SWP_NOMOVE, SWP_NOSIZE = 0x0002, 0x0001


def wait_main_window(timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value.strip() == "CQUFSAE串口助手":
                    found.append(hwnd)
            return True

        user32.EnumWindows(cb, 0)
        if found:
            return found[0]
        time.sleep(0.3)
    return 0


def bring_front(hwnd) -> bool:
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    if user32.GetForegroundWindow() == hwnd:
        return True
    # 前台锁拒绝时用 TOPMOST 顶一下，再撤掉 TOPMOST（否则会挡住设置面板）
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    return user32.GetForegroundWindow() == hwnd


def _own_window(hwnd) -> bool:
    """只认**我们自己的**窗口：系统里本来就有一个叫「设置」的应用。"""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return False
        return buf.value.lower().endswith("serialassistant.exe")
    finally:
        kernel32.CloseHandle(handle)


def find_settings():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value.strip() == "设置" and user32.IsWindowVisible(hwnd) \
                    and _own_window(hwnd):
                found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else 0


def grab(hwnd, path) -> tuple[int, int]:
    from PySide6.QtGui import QGuiApplication
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    screen = QGuiApplication.primaryScreen()
    image = screen.grabWindow(0, rect.left, rect.top, width, height).toImage()
    image.save(path)
    return width, height


def main() -> int:
    import subprocess

    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication(sys.argv)   # 截图需要 app 实例
    assert app is not None

    settings = QSettings("MimoWorks", "SerialAssistant")
    saved_geometry = settings.value("geometry")
    saved_startup = settings.value("custom/startup_maximized")
    settings.remove("geometry")
    settings.setValue("custom/startup_maximized", False)
    settings.sync()

    process = subprocess.Popen([EXE])
    try:
        hwnd = wait_main_window()
        if not hwnd:
            print("FAIL 主窗口没起来")
            return 1
        if not bring_front(hwnd):
            print("WARN 没能抢到前台，仍然尝试点击")
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        x = rect.left + 64              # GAP12 + 内边距6 + 半个 28px 按钮，×2 缩放
        y = rect.bottom - 56
        user32.SetCursorPos(x, y)
        time.sleep(0.3)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        for _ in range(20):
            dialog = find_settings()
            if dialog:
                time.sleep(0.6)         # 让入场动画落地
                width, height = grab(dialog, OUT)
                print(f"OK 设置面板 {width}x{height} → {OUT}")
                return 0
            time.sleep(0.3)
        print("FAIL 没找到设置面板窗口")
        return 1
    finally:
        process.terminate()
        time.sleep(0.5)
        if saved_geometry is None:
            settings.remove("geometry")
        else:
            settings.setValue("geometry", saved_geometry)
        if saved_startup is None:
            settings.remove("custom/startup_maximized")
        else:
            settings.setValue("custom/startup_maximized", saved_startup)
        settings.sync()


if __name__ == "__main__":
    raise SystemExit(main())
