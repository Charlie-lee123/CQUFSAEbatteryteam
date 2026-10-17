# -*- coding: utf-8 -*-
"""临时探针：驱动**打包产物**，量接收区功能列在普通态/展开态的真实布局。

为什么走 exe：本会话里从源码 show() 主窗口会访问违故（exit 127，环境问题），
而 exe 用户跑着没事 —— 探针就该测用户看到的那一个。
"""
import ctypes
import ctypes.wintypes as wt
import os
import struct
import subprocess
import sys
import time
import zlib

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
ctypes.windll.user32.SetProcessDPIAware()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_check")
EXE = os.path.join(ROOT, "发布", "SerialAssistant.exe")
os.makedirs(OUT, exist_ok=True)

user32 = ctypes.windll.user32


def find_window():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if "串口调试助手" in buf.value:
                found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def grab(name):
    from PySide6.QtGui import QGuiApplication
    pix = QGuiApplication.primaryScreen().grabWindow(0)
    path = os.path.join(OUT, name)
    pix.save(path)
    print("   截图:", path, flush=True)
    return path


def load_png(path):
    """手动解 PNG 成 (w, h, 行暗像素计数 list, 列暗像素计数 list)。"""
    with open(path, "rb") as f:
        data = f.read()
    pos = 8
    idat = b""
    w = h = ct = None
    while pos < len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            w, h, bd, ct = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"IDAT":
            idat += chunk
        pos += 12 + length
    raw = zlib.decompress(idat)
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]
    stride = w * ch
    rows = bytearray()
    prev = bytearray(stride)
    p = 0
    for _ in range(h):
        ft = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        if ft == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        rows += line
        prev = line

    # 当前主题可能是浅色（theme_mode=system 且系统是浅色）→ 图标是深色描边；
    # 也可能是深色 → 图标是亮描边。两种都算：与背景反差大的像素。
    # 先估背景色（四角均值），再数"与背景差 > 60"的像素。
    bg = [0, 0, 0]
    for (x, y) in ((2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3)):
        i = y * stride + x * ch
        bg[0] += rows[i]
        bg[1] += rows[i + 1]
        bg[2] += rows[i + 2]
    bg = [c // 4 for c in bg]
    rowcnt = [0] * h
    colcnt = [0] * w
    for y in range(h):
        base = y * stride
        for x in range(w):
            i = base + x * ch
            d = abs(rows[i] - bg[0]) + abs(rows[i + 1] - bg[1]) + abs(rows[i + 2] - bg[2])
            if d > 150:
                rowcnt[y] += 1
                colcnt[x] += 1
    return w, h, rowcnt, colcnt, bg


def segments(counts, min_gap=8, min_len=6):
    segs = []
    start = None
    gap = 0
    for i, c in enumerate(counts):
        if c > 2:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                if i - gap - start >= min_len:
                    segs.append((start, i - gap - 1))
                start = None
    if start is not None and len(counts) - start >= min_len:
        segs.append((start, len(counts) - 1))
    return segs


def report(path, tag):
    w, h, rowcnt, colcnt, bg = load_png(path)
    print(f"[{tag}] {w}x{h} 背景色~{bg}", flush=True)
    colsegs = segments(colcnt)
    # 最右侧的连续列段 = 功能列
    print(f"   列段: {[(a, b) for a, b in colsegs][-6:]}", flush=True)
    rowsegs = segments(rowcnt)
    print(f"   行段数 {len(rowsegs)}: {rowsegs[:20]}", flush=True)
    return w, h, colsegs, rowsegs


def main() -> int:
    from PySide6.QtWidgets import QApplication
    _app = QApplication(sys.argv)      # 只为拿屏幕 grab

    proc = subprocess.Popen([EXE])
    print("started pid", proc.pid, flush=True)
    hwnd = None
    deadline = time.time() + 90
    while time.time() < deadline:
        hwnd = find_window()
        if hwnd:
            break
        time.sleep(2)
    if not hwnd:
        print("FAIL 没找到窗口", flush=True)
        return 1
    time.sleep(6)
    r = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    print(f"窗口矩形: {r.left},{r.top} ~ {r.right},{r.bottom} "
          f"({r.right - r.left}x{r.bottom - r.top})", flush=True)

    # ① 普通态（启动即整窗全屏）
    p1 = grab("railx_1_normal.png")
    w1, h1, colsegs1, rowsegs1 = report(p1, "普通态")

    # 找功能列：最右侧列段的中心 x
    rail_x = None
    for a, b in reversed(colsegs1):
        if b - a < 80:              # 功能列很窄（~30px + 图标笔画）
            rail_x = (a + b) // 2
            break
    if rail_x is None:
        print("FAIL 找不到功能列", flush=True)
        subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"],
                       capture_output=True)
        return 1
    print(f"   功能列中心 x ≈ {rail_x}", flush=True)

    # 展开键 = 行段里最靠下的那一颗（7 颗里最后一颗）
    icon_segs = [s for s in rowsegs1 if 5 <= s[1] - s[0] <= 40]
    if not icon_segs:
        print("FAIL 找不到图标行段", flush=True)
        subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"],
                       capture_output=True)
        return 1
    last = icon_segs[-1]
    click_y = (last[0] + last[1]) // 2
    print(f"   点展开键 ({rail_x}, {click_y})", flush=True)

    user32.SetForegroundWindow(wt.HWND(hwnd))
    time.sleep(0.4)
    user32.SetCursorPos(rail_x, click_y)
    time.sleep(0.3)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(2.0)

    # ② 展开态
    p2 = grab("railx_2_expanded.png")
    report(p2, "展开态")

    # ③ Esc 收起
    user32.SetForegroundWindow(wt.HWND(hwnd))
    time.sleep(0.3)
    user32.keybd_event(0x1B, 0, 0, 0)
    user32.keybd_event(0x1B, 0, 0x0002, 0)
    time.sleep(2.0)
    p3 = grab("railx_3_collapsed.png")
    report(p3, "收起后")

    subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"],
                   capture_output=True)
    print("exe 已退出", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
