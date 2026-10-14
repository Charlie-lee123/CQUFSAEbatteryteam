# -*- coding: utf-8 -*-
r"""给安装向导拍一张真窗口截图（给 README 用 + 肉眼核对主题化效果）。

只开向导看一眼就关：**不装任何东西**（不带 /DIR、不点下一步），窗口关掉
就地退出。关窗用 WM_CLOSE + /SUPPRESSMSGBOXES —— 后者会让 Inno 的
"确定要退出安装吗"自动应答，不会卡在确认框上。

用法：
    python devtools\_installer_shot.py [输出.png]
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = os.path.join(ROOT, "发布", "SerialAssistant-setup-2.0.exe")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "_check", "installer_wizard.png")

user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.WinDLL("gdi32")


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def pid_image_path(pid: int) -> str:
    size = wt.DWORD(1024)
    buf = ctypes.create_unicode_buffer(size.value)
    kernel32 = ctypes.WinDLL("kernel32")
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        try:
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value
        finally:
            kernel32.CloseHandle(h)
    return ""


def find_wizard_windows(image_name: str):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if user32.IsWindowVisible(hwnd) and owner.value:
            image = os.path.basename(pid_image_path(owner.value)).lower()
            if image == image_name or image.startswith(image_name):
                length = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value.strip():
                    found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return found


def capture(hwnd, path: str) -> bool:
    """用 Qt 的 grabWindow 抓原生窗口（自己拿 DIB 再拼会翻转/串位，踩过）。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    from PySide6.QtGui import QGuiApplication
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    screen = QGuiApplication.primaryScreen()
    pixmap = screen.grabWindow(hwnd)            # 传原生窗口句柄
    if pixmap.isNull():
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return pixmap.save(path, "PNG")


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    from PySide6.QtGui import QGuiApplication
    _app = QGuiApplication(sys.argv)          # grabWindow 需要一个 GUI 实例
    if not os.path.exists(SETUP):
        print("✗ 找不到安装包:", SETUP)
        return 1
    proc = subprocess.Popen([SETUP, "/SUPPRESSMSGBOXES", "/CURRENTUSER"])
    try:
        deadline = time.time() + 25
        hwnd = None
        while time.time() < deadline and hwnd is None:
            time.sleep(0.4)
            for handle, text in find_wizard_windows("serialassistant-setup-2.0"):
                hwnd, _title = handle, text
                break
        if hwnd is None:
            print("✗ 25 秒内没等到向导窗口")
            return 1
        time.sleep(1.2)                       # 等它画完（图和文字都在）
        if capture(hwnd, OUT):
            print("截图 →", OUT)
            return 0
        print("✗ 抓窗口失败")
        return 1
    finally:
        # 关窗：窗口可能已经自己退出（找不到就别硬找，别让收尾把主流程炸了）
        try:
            wins = find_wizard_windows("serialassistant-setup-2.0")
            if wins:
                user32.PostMessageW(wins[0][0], 0x0010, 0, 0)   # WM_CLOSE
                time.sleep(1.5)
        finally:
            if proc.poll() is None:
                proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
