# -*- coding: utf-8 -*-
"""打包产物实测：启动「发布」文件夹里的 exe，等窗口出来，用 PrintWindow 抓真窗口。"""
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

# 命令行进程必须先声明 DPI 感知，否则拿到的窗口是 2 倍物理像素、画布按逻辑尺寸建，
# PrintWindow 会把画面裁掉一半。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()
ctypes.windll.user32.SetProcessDPIAware()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_check")
# 产物在项目下的「发布」文件夹里（不是 PyInstaller 默认的 dist/）—— 这个名字一眼
# 就能找到，要发给别人时直接把整个文件夹打包或把 exe 拖出去就行。
RELEASE_DIR = os.path.join(ROOT, "发布")
EXE = os.path.join(RELEASE_DIR, "SerialAssistant.exe")

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def _pid_image_path(pid):
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def windows_of(pid):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return found


def our_windows():
    """单文件模式会派生一个无窗口的引导进程，窗口属于子进程 ——
    所以不能按父进程 pid 找，要按进程映像名找。"""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if user32.IsWindowVisible(hwnd) and owner.value:
            image = _pid_image_path(owner.value).lower()
            if image.endswith("serialassistant.exe"):
                length = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value.strip():
                    found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return found


def capture(hwnd):
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    hdc = user32.GetWindowDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(memdc, bmp)
    ok = user32.PrintWindow(hwnd, memdc, 2)     # 2 = PW_RENDERFULLCONTENT
    info = BITMAPINFOHEADER()
    info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.biWidth, info.biHeight = width, -height
    info.biPlanes, info.biBitCount, info.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memdc, bmp, 0, height, buf, ctypes.byref(info), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(hwnd, hdc)
    return bytes(buf.raw), width, height, ok


def send_ctrl_f(hwnd):
    """把 Ctrl+F 真发进打包后的窗口，验证搜索键在产物里也是活的。

    用 keybd_event 而不是 PostMessage(WM_KEYDOWN)：Qt 判断修饰键读的是
    GetKeyState，投递的消息不会改这个状态，Ctrl+F 会被当成单个 F 丢掉。
    所以必须真的抢焦点 + 真的敲键盘。
    """
    VK_CONTROL, VK_F, KEYUP = 0x11, 0x46, 0x0002
    user32.ShowWindow(hwnd, 9)                      # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.8)
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_F, 0, 0, 0)
    user32.keybd_event(VK_F, 0, KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYUP, 0)
    time.sleep(1.0)


def send_key(vk):
    KEYUP = 0x0002
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYUP, 0)
    time.sleep(0.6)


def click_at(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.3)
    user32.mouse_event(0x0002, 0, 0, 0, 0)          # LEFTDOWN
    user32.mouse_event(0x0004, 0, 0, 0, 0)          # LEFTUP


def open_settings(hwnd):
    """点左下角的设置按钮打开设置面板，返回那个窗口的句柄。

    不去"猜"按钮的像素位置：按版式常量算出一个大概位置，然后**用结果验证** ——
    只有真的多出一个标题为「设置」的顶层窗口才算点中，没中就挪几个点再试。
    """
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    # 先把窗口抢到前台再点：SetCursorPos + mouse_event 是"真的在屏幕上点"，
    # 一旦有别的窗口压在上面（比如跑测试的终端），点到的就是那个窗口 —— 
    # 这一步以前偶发失败就是因为没抢焦点。
    user32.ShowWindow(hwnd, 9)                       # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.6)
    # 版式：窗口左下留白 GAP-SHADOW_PAD=12，信息条左内边距 6，28px 的按钮取中心。
    # GetWindowRect 给的是物理像素，屏幕缩放已由 SetProcessDpiAwareness 声明过。
    scale = 1.0
    try:
        scale = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100.0
    except Exception:
        pass
    base_x = rect.left + (12 + 6 + 14) * scale
    base_y = rect.bottom - (12 + 2 + 14) * scale
    for dx, dy in ((0, 0), (14, 0), (-14, 0), (0, -14), (14, -14),
                   (-14, -14), (0, -28), (14, -28)):
        click_at(base_x + dx, base_y + dy)
        time.sleep(1.3)
        for candidate, title in our_windows():
            if title.strip() == "设置":
                return candidate
    return None


def save_png(pixels, width, height, name):
    from PySide6.QtGui import QGuiApplication, QImage
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)   # PNG 插件需要 app 实例
    image = QImage(pixels, width, height, QImage.Format.Format_ARGB32).copy()
    path = os.path.join(OUT, name)
    print("saved:", image.save(path), path)
    return image, app


def main() -> None:
    """产物实测入口：先把窗口尺寸钉死，再跑，跑完把配置还回去。

    窗口尺寸会从注册表恢复（上一次干净关闭留下的），而下面这些按比例裁的图
    **对窗口宽度敏感** —— 实测被存下的 1600×930 一带，接收区就宽出去一大截，
    「时钟」那块直接裁到了卡片右边的空白上（前景占比 0.011，门槛 0.03）。
    所以先摘掉 geometry 让产物用默认尺寸起身（产物是被 taskkill 掉的，
    不会自己写回配置，所以这里必须自己还）。
    """
    from PySide6.QtCore import QSettings

    settings = QSettings("MimoWorks", "SerialAssistant")
    saved_geometry = settings.value("geometry")
    saved_theme = settings.value("theme_mode")
    saved_startup = settings.value("custom/startup_maximized")
    settings.remove("geometry")
    # 主题钉成深色：下面的前景占比等量化断言是按深色校准的，exe 跟着系统
    # 走（theme_mode="system"）的话，白天系统切浅色整份检查就全乱了。
    settings.setValue("theme_mode", "dark")
    # 让产物以**普通窗口**起手：下面的布局量化都按普通窗口校准，而"发 Esc 退出
    # 启动全屏"依赖前台焦点（SetForegroundWindow 会被系统的前台锁拒绝），实测
    # 偶发失败时会把**全屏尺寸**的图按普通窗口坐标裁，整份检查全乱。
    # "打开即全屏"这件事由 _probe_exe_startup.py 单独验，这里不混着测。
    settings.setValue("custom/startup_maximized", False)
    settings.sync()
    try:
        run()
    finally:
        if saved_geometry is None:
            settings.remove("geometry")
        else:
            settings.setValue("geometry", saved_geometry)
        if saved_theme is None:
            settings.remove("theme_mode")
        else:
            settings.setValue("theme_mode", saved_theme)
        if saved_startup is None:
            settings.remove("custom/startup_maximized")
        else:
            settings.setValue("custom/startup_maximized", saved_startup)
        settings.sync()


def run() -> None:
    os.makedirs(OUT, exist_ok=True)
    for old in os.listdir(OUT):
        if old.startswith("exe_"):
            os.remove(os.path.join(OUT, old))

    # 记下产物日志当前的长度：跑完之后只看**本次新增**的那一段，用来查
    # "窗口图标素材在不在产物里"（见下面 exe_caption_left 那段）。
    log_path = os.path.join(os.path.dirname(EXE), "logs", "serialassistant.log")
    log_offset = os.path.getsize(log_path) if os.path.exists(log_path) else 0

    proc = subprocess.Popen([EXE])
    print("started pid", proc.pid)

    # 桌面快捷方式必须指着**当前**产物：产物一换目录（dist -> 发布）它就不会自愈，
    # 双击静默打不开（用户报过一次"我的桌面快捷方式也要保留啊"）。这里只报告，
    # 不算 FAIL —— 桌面环境因人而异；修它跑 `_desktop_shortcut.py`。
    try:
        from _desktop_shortcut import LINK_NAME, desktop_dir, read_target

        link = os.path.join(desktop_dir(), LINK_NAME)
        if os.path.exists(link):
            target = read_target(link)
            ok = os.path.normcase(os.path.abspath(target)) == \
                os.path.normcase(os.path.abspath(EXE))
            print(f"桌面快捷方式 -> {target}  {'OK' if ok else '<<< 指错了'}")
            if not ok:
                print("WARN 桌面快捷方式没指向当前产物（跑 _desktop_shortcut.py 修）")
        else:
            print(f"桌面没有 {LINK_NAME}（要创建就跑 _desktop_shortcut.py）")
    except Exception as exc:                       # noqa: BLE001
        print("  （跳过桌面快捷方式检查）", exc)

    target = None
    deadline = time.time() + 90          # 单文件冷启动要解包，给足时间
    while time.time() < deadline:
        candidates = our_windows()
        if candidates:
            target = candidates[0]
            break
        time.sleep(2)
    if not target:
        print("FAIL 没找到窗口")
        subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"],
                       capture_output=True)
        return

    hwnd, title = target
    print("window title:", repr(title))
    time.sleep(4)                        # 等入场动画与首帧稳定
    # 产物这次是**普通窗口**起手的（main() 里临时把「启动时最大化」关掉了，
    # 原因见那里的说明）。这里确认一下，别拿全屏尺寸的图按普通窗口的坐标裁。
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    if user32.GetSystemMetrics(0) - (rect.right - rect.left) < 100:
        print("FAIL 产物起手仍是全屏（临时关掉「启动时最大化」没生效？）")
        subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"],
                       capture_output=True)
        return
    pixels, width, height, ok = capture(hwnd)
    print(f"PrintWindow ok={ok} size={width}x{height}")
    img, _app = save_png(pixels, width, height, "exe_run.png")

    # 采样几个点，确认不是一张全黑/空白的图
    for name, (x, y) in {"卡面": (int(width * 0.3), int(height * 0.3)),
                         "文字区": (int(width * 0.2), int(height * 0.42)),
                         "背景右下": (int(width * 0.97), int(height * 0.9))}.items():
        c = img.pixelColor(x, y)
        print(f"  {name} ({x},{y}) = {c.red()},{c.green()},{c.blue()}")

    # 本轮新增元素的产物验证。接收区右上角的实时时钟位置随窗口尺寸变，所以
    # 按比例取一块，数"明显不是背景"的像素 —— 不认具体颜色，亮暗两套主题都成立。
    from PySide6.QtCore import QRect as _QRect

    def ink_ratio(image):
        counts, points = {}, []
        for y in range(0, image.height(), 2):
            for x in range(0, image.width(), 2):
                c = image.pixelColor(x, y)
                key = (c.red(), c.green(), c.blue())
                points.append(key)
                counts[key] = counts.get(key, 0) + 1
        if not points:
            return 0.0
        background = max(counts.items(), key=lambda kv: kv[1])[0]
        ink = sum(1 for p in points
                  if max(abs(p[i] - background[i]) for i in range(3)) > 60)
        return ink / len(points)

    # exe_txrow 这一块：发送区现在**开局是折叠的**（只剩标题栏 + 展开箭头），
    # 所以这张图拍到的是那条收起来的标题栏、不是输入框那排 —— 想看展开态的
    # 版式去看 _render_check 的 tx_oneline_*.png（那个会主动展开）。
    for name, box in {
        "exe_clock.png": (0.58, 0.22, 0.22, 0.07),
        "exe_config.png": (0.70, 0.215, 0.30, 0.44),
        "exe_txrow.png": (0.02, 0.80, 0.72, 0.15),
        "exe_bar.png": (0.00, 0.952, 0.30, 0.048),
    }.items():
        x, y, w, h = box
        piece = img.copy(_QRect(int(width * x), int(height * y),
                                int(width * w), int(height * h)))
        piece.save(os.path.join(OUT, name))
        print(f"  {name} 前景占比 {ink_ratio(piece):.3f}")
    clock_piece = img.copy(_QRect(int(width * 0.58), int(height * 0.22),
                                  int(width * 0.22), int(height * 0.07)))
    if ink_ratio(clock_piece) < 0.03:
        print("FAIL 接收区右上角没找到时钟字样")
        subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"],
                       capture_output=True)
        return

    # 自绘标题栏左端的窗口图标（用户："我的 logo 呢，logo 得保留啊"）。窗口改成
    # 无边框之后系统标题栏没了，那枚图标得自己画回来。
    #
    # ⚠️ 这里**不按客户区坐标取像素**：无边框窗口 + DwmExtendFrameIntoClientArea 让
    # PrintWindow 的图相对客户区整体偏移了二十多物理像素（实测图标落在 73..103，
    # 而按 (CARD_PAD+SHADOW_PAD)*scale = 48..80 去取，取到的是一片背景 —— 第一版
    # 就是这么误报的）。所以：
    #   - 素材在不在产物里 → 查日志：app_icon() 加载失败会写一行 WARNING（见 ui_brand）
    #   - 图标画没画出来   → 由 selftest 逐像素断言（含"图标与文字之间必须留白"
    #                        那条，专挡"文字左移顶上去"的假绿）
    # 这里只留一张标题栏左端的截图供目视。
    from ui_brand import CaptionBar as _CaptionBar
    from ui_theme import CAPTION_H as _CAPTION_H

    scale = 1.0
    try:
        user32.GetDpiForWindow.argtypes = [wt.HWND]
        dpi = user32.GetDpiForWindow(wt.HWND(hwnd))
        if dpi:
            scale = dpi / 96.0
    except Exception:
        pass
    bar_box = _QRect(0, 0, int(240 * scale), int(_CAPTION_H * scale))
    img.copy(bar_box).save(os.path.join(OUT, "exe_caption_left.png"))
    print(f"  exe_caption_left 已存（缩放 {scale:.2f}，图标 {_CaptionBar.ICON_PX}px）")

    log_path = os.path.join(os.path.dirname(EXE), "logs", "serialassistant.log")
    if os.path.exists(log_path):
        with open(log_path, "rb") as handle:
            handle.seek(log_offset)
            fresh = handle.read().decode("utf-8", "replace")
        if "窗口图标加载失败" in fresh:
            print("FAIL 产物里找不到窗口图标素材（assets/app.ico 没打进 exe）")
            subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"],
                           capture_output=True)
            return
        print("  窗口图标素材 OK（本次运行日志里没有加载失败记录）")
    else:
        print("  （没找到产物日志，跳过图标素材检查）")

    # 底栏那颗齿轮"悬停也不铺底色"**不在这里验**：那要靠真把鼠标挪到按钮上，
    # 而这台机器上光标随时可能被真人挪走（实测 SetCursorPos 返回成功、
    # 读回来却在 (1115,888)/(1471,789)/(553,1568) 之间跳），断言必然随机失败。
    # 该行为由 selftest 在 offscreen 下把控件渲染成图逐像素判定（确定性），
    # 外加 _render_check 的 bottom_bar_*.png 目视，产物这边不重复。

    # 真实敲一下 Ctrl+F，抓第二张。展开后接收区多出一整行查找栏，
    # 日志框顶部会下移，两张图对比能直接看出来。
    send_ctrl_f(hwnd)
    pixels2, width2, height2, ok2 = capture(hwnd)
    print(f"PrintWindow(search) ok={ok2} size={width2}x{height2}")
    save_png(pixels2, width2, height2, "exe_search.png")

    # 关掉查找栏，再点开设置面板 —— 验证「界面按钮」分组（本轮加到 7 项，
    # 含「编码」「历史」两个整行）在**打包后的产物**里也真的画出来了
    send_key(0x1B)                                   # VK_ESCAPE
    dialog = open_settings(hwnd)
    if dialog is None:
        print("FAIL 没能打开设置面板（没找到标题为「设置」的窗口）")
    else:
        print("settings dialog hwnd:", dialog)
        time.sleep(1.5)
        pix, dw, dh, dok = capture(dialog)
        print(f"PrintWindow(settings) ok={dok} size={dw}x{dh}")
        save_png(pix, dw, dh, "exe_settings.png")
        # 面板里开关是蓝色的（开启态），横着扫一条找蓝色像素，
        # 找到就说明「界面按钮」那组开关真的画出来了、不是空面板。
        # 本轮清单从 5 项加到 7 项，所以门槛按比例提高 —— 只画出来一半是过不了的。
        shot = pix
        found = 0
        for y in range(int(dh * 0.45), int(dh * 0.95), 3):
            for x in range(int(dw * 0.05), int(dw * 0.95), 3):
                off = (y * dw + x) * 4
                b, g, r = shot[off], shot[off + 1], shot[off + 2]
                if b > 180 and b - r > 60 and b - g > 40:
                    found += 1
        print(f"蓝色开关像素数: {found}（5 项那版约 50，7 项应当明显更多）")
        if found < 120:
            print("FAIL 设置面板里开关太少（蓝色像素不足，可能没画出新增的「编码」「历史」）")
            subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"],
                           capture_output=True)
            return
        send_key(0x1B)                               # 关掉设置面板
        time.sleep(0.8)

        # 接收区悬停：往日志区挪一圈鼠标并点一下。产物里没有串口数据，图标不会浮出来，
        # 这里验证的是"事件过滤器在真窗口里不炸、窗口还在" —— 图标的定位与复制行为
        # 由 selftest（真实事件分发）和 _render_check（真实渲染）覆盖。
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        for fx, fy in ((0.25, 0.35), (0.45, 0.42), (0.62, 0.48), (0.30, 0.52)):
            click_at(rect.left + (rect.right - rect.left) * fx,
                     rect.top + (rect.bottom - rect.top) * fy)
        time.sleep(0.6)
        alive = any(h for h, _t in our_windows())
        print("悬停/点击接收区后窗口仍在:", alive)
        if not alive:
            print("FAIL 接收区悬停把窗口搞崩了")
        pixels3, width3, height3, ok3 = capture(hwnd)
        save_png(pixels3, width3, height3, "exe_rx_hover.png")

        subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"],
                       capture_output=True)
        time.sleep(1)
        print("exe 已退出")
        return

    proc.terminate()
    subprocess.run(["taskkill", "/F", "/IM", "SerialAssistant.exe"], capture_output=True)
    time.sleep(1)
    print("exe 已退出")


if __name__ == "__main__":
    main()
