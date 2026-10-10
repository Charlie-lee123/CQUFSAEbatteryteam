"""临时探针：真鼠标测"拖标题栏移动窗口"和"拖窗口边缘缩放"。

两个都走系统那套（`startSystemMove` / `startSystemResize` → WM_NCLBUTTONDOWN），
而系统在收到这个消息时会读**真实的按键状态**（GetAsyncKeyState）—— 所以必须用
mouse_event 真按下再移动，合成 QMouseEvent 是测不出来的。
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys

# 脚本挪进 devtools/ 之后，运行时项目根不再自动在 sys.path 里 —— 补上
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
ctypes.windll.user32.SetProcessDPIAware()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "SerialAssistant.exe")

user32 = ctypes.windll.user32
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]


def rect_of(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def find_window():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.strip() == "CQUFSAE串口调试助手":
                found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def press(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.25)
    user32.mouse_event(0x0002, 0, 0, 0, 0)      # LEFTDOWN
    time.sleep(0.25)


def move_to(x, y, steps=12):
    for i in range(1, steps + 1):
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.03)


def release():
    user32.mouse_event(0x0004, 0, 0, 0, 0)      # LEFTUP
    time.sleep(0.4)


def main() -> None:
    proc = subprocess.Popen([EXE])
    hwnd = None
    deadline = time.time() + 90
    while time.time() < deadline:
        hwnd = find_window()
        if hwnd:
            break
        time.sleep(2)
    if not hwnd:
        print("FAIL 没找到窗口")
        return
    time.sleep(6)
    # 退成普通窗口。Esc 是发给**前台窗口**的，启动全屏那一下焦点可能还没落到本程序
    # 身上 —— 所以按一次之后要比一比矩形，还在满屏就再按（最多三次）。
    for _attempt in range(3):
        user32.SetForegroundWindow(wt.HWND(hwnd))
        time.sleep(0.6)
        user32.keybd_event(0x1B, 0, 0, 0)
        user32.keybd_event(0x1B, 0, 0x0002, 0)
        time.sleep(2.0)
        if rect_of(hwnd) != (0, 0, 3200, 2000):
            break
    start = rect_of(hwnd)
    print("普通态:", start)
    if start == (0, 0, 3200, 2000):
        print("FAIL 没能退出启动全屏（Esc 没落到本程序上），后面的量测没意义")
        subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"],
                       capture_output=True)
        return

    # ---- 1) 先测右下角缩放 ----
    # ⚠️ 必须在**移动窗口之前**测：先把窗口拖到下方，它的右下角会跑到屏幕外
    # （这块屏只有 2000 高），SetCursorPos 够不到那个角，测试会假挂一次。
    pre = rect_of(hwnd)
    x, y = pre[2] - 2, pre[3] - 2
    press(x, y)
    move_to(x - 180, y - 140)
    release()
    post = rect_of(hwnd)
    print("拖右下角之前:", pre)
    print("拖右下角之后:", post,
          "-> 宽", post[2] - post[0], "(原", pre[2] - pre[0], ")",
          "高", post[3] - post[1], "(原", pre[3] - pre[1], ")")
    print("  缩放判定:", "OK" if (post[2] - post[0]) < (pre[2] - pre[0]) - 80
          and (post[3] - post[1]) < (pre[3] - pre[1]) - 60 else "FAIL（没缩放）")

    # ---- 2) 再测拖标题栏移动窗口（往上/往左拖，别把窗口推出屏幕）----
    base = rect_of(hwnd)
    x = base[0] + 120                      # 标题栏左半段（图标/文字区域）
    y = base[1] + 17
    press(x, y)
    move_to(x + 120, y - 80)
    release()
    moved = rect_of(hwnd)
    print("拖标题栏之后:", moved,
          "-> 位移", (moved[0] - base[0], moved[1] - base[1]))
    print("  移动判定:", "OK" if moved[0] > base[0] + 60 and moved[1] < base[1] - 40
          else "FAIL（没动）")

    subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"],
                   capture_output=True)
    print("exe 已退出")


if __name__ == "__main__":
    main()
