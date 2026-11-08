"""后台窗口快照采集（od47i）：用 Win32 BitBlt 在独立线程抓当前窗口，零主线程阻塞。

为什么需要它（用户 2026-11-05「最大化/还原还是卡/闪/跳变」根因）：
- 旧覆盖层 A/B 两层快照来自「现场同步抓」或「1.5s 前的空闲缓存」：
  · 现场同步抓（_grab_window_rect）在主线程阻塞 ~51~114ms = 卡；
  · 1.5s 前的缓存（_fs_idle_pix）与实时滚动的串口数据差太多 = 闪/跳变。
- 两者互斥：要么卡，要么闪。串口助手窗口内容是**实时滚动**的，任何
  「旧快照」都会在动画起手/收尾露出内容错位 —— 快照覆盖层对实时内容窗口
  天生有此缺陷，唯一正解是「用接近实时的新鲜快照 + 零主线程阻塞」。
- 本模块在后台线程用 BitBlt 按 ~160ms 周期抓当前窗口矩形，主线程随时可取
  「≤160ms 前」的新鲜快照（串口数据 160ms 滚动量很小，肉眼基本无错位），
  且抓图全程不碰主线程 → 既不卡也不闪。

⚠️ 只用 Win32 GDI（BitBlt / GetDC），不调用任何 Qt GUI 接口：
   QImage 构造/拷贝可在非 GUI 线程安全进行；抓到的 QImage 经信号（QImage 是
   隐式共享，跨线程传递只增引用计数不拷像素）交给主线程，跨线程安全。
⚠️ 抓取方式是**桌面 DC + 屏幕坐标 BitBlt**（不是 GetWindowDC(hwnd)）。原因：
   GetWindowDC 对 DWM 合成窗口只拿到空 GDI 表面 = 纯黑（真机实测）。桌面 DC
   拿的是 DWM 合成后的画面，与 QScreen.grabWindow(0, x, y, w, h) 同法。
⚠️ 内存布局：DIB 32bpp BI_RGB 是 [B,G,R,X]，QImage Format_RGB32 在小端机上
   是 [B,G,R,X]（0xffRRGGBB 的存储序）——**两者一致，绝不能 rgbSwapped()**。
   曾经这里多做过一次 rgbSwapped()，导致红蓝互换（界面几乎全灰，肉眼没查出，
   由 _check/_probe_snap_color.py 用纯色窗口实测暴露）。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

# 采集周期（ms）：越小越新鲜但越费；160ms 下串口数据滚动量极小，肉眼无错位。
_SNAP_INTERVAL_MS = 160
# 安全上限：避免极端尺寸（如极小/逻辑异常）时分配爆炸。
_MAX_PIXELS = 12_000_000


def _capture_window_qimage(hwnd: int) -> QImage | None:
    """在调用线程内用 BitBlt 抓 hwnd 自身矩形，返回物理像素 QImage（RGB32/不透明）。"""
    if not hwnd:
        return None
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        rect = wt.RECT()
        if not user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect)):
            return None
        x, y = int(rect.left), int(rect.top)
        w = max(1, int(rect.right - rect.left))
        h = max(1, int(rect.bottom - rect.top))
        if w * h > _MAX_PIXELS:
            return None

        # ⛔⛔ 绝不要给这些函数设 restype=c_void_p / HDC（"修正 64 位句柄截断"的
        #    直觉在这里是**错的**，实测把成功率从 100% 打到 15%）。原因：
        #    ctypes.windll 默认 restype=c_int，GDI 句柄高位为 1 时返回**负数**，
        #    但后续调用（同样无 argtypes）按 c_int 回写，**位模式前后一致**，正确。
        #    一旦把某处 restype 声明成 c_void_p，ctypes 会把负数符号扩展成
        #    0xFFFFFFFFxxxxxxxx 这个不存在的 64 位句柄，再传给 BitBlt 直接
        #    OverflowError: int too long to convert。要么全部按 c_int 不动，
        #    要么给每个参数都写 argtypes —— 这里选前者。
        #    验证脚本：_check/_probe_snap_color.py、_check/_probe_snap_loop.py。

        # ⛔⛔ 必须用**桌面 DC + 屏幕坐标 BitBlt**（见模块 docstring）。
        desktop = user32.GetDC(wt.HWND(0))
        if not desktop:
            return None
        mem_dc = None
        bmp = None
        old_obj = None
        try:
            mem_dc = gdi32.CreateCompatibleDC(desktop)
            if not mem_dc:
                return None
            bmp = gdi32.CreateCompatibleBitmap(desktop, w, h)
            if not bmp:
                return None
            old_obj = gdi32.SelectObject(mem_dc, bmp)
            # SRCCOPY = 0x00CC0020；源坐标为窗口在屏幕上的物理坐标。
            if not gdi32.BitBlt(mem_dc, 0, 0, w, h, desktop, x, y, 0x00CC0020):
                return None

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", ctypes.c_ulong),
                    ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long),
                    ("biPlanes", ctypes.c_short),
                    ("biBitCount", ctypes.c_short),
                    ("biCompression", ctypes.c_ulong),
                    ("biSizeImage", ctypes.c_ulong),
                    ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", ctypes.c_ulong),
                    ("biClrImportant", ctypes.c_ulong),
                ]

            bih = BITMAPINFOHEADER()
            bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bih.biWidth = w
            bih.biHeight = -h  #  top-down
            bih.biPlanes = 1
            bih.biBitCount = 32
            bih.biCompression = 0  # BI_RGB
            buf = ctypes.create_string_buffer(w * h * 4)
            # ⛔ GetDIBits 成功返回扫描行数 h；失败返回 0。不检查的话 buf 仍是
            #    create_string_buffer 的全 0 → 得到一张"合法"的全黑图，会被
            #    覆盖层当成有效帧用出去 = 动画里黑屏一闪。
            if int(gdi32.GetDIBits(mem_dc, bmp, 0, h, buf,
                                   ctypes.byref(bih), 0) or 0) != h:
                return None
        finally:
            # 异常路径也必须释放，否则 160ms 周期抓图会持续泄漏 GDI 句柄
            # （进程句柄上限 10000，泄漏到顶后整个界面无法绘制）。
            if mem_dc and old_obj:
                try:
                    gdi32.SelectObject(mem_dc, old_obj)
                except Exception:  # noqa: BLE001
                    pass
            if bmp:
                try:
                    gdi32.DeleteObject(bmp)
                except Exception:  # noqa: BLE001
                    pass
            if mem_dc:
                try:
                    gdi32.DeleteDC(mem_dc)
                except Exception:  # noqa: BLE001
                    pass
            try:
                user32.ReleaseDC(wt.HWND(0), desktop)
            except Exception:  # noqa: BLE001
                pass

        # DIB 32bpp BI_RGB 内存布局 [B,G,R,X] 与 QImage Format_RGB32 在小端机上
        # 完全一致 → 直接解释即可，**不要 rgbSwapped()**（会红蓝互换）。
        # copy() 让 QImage 拥有独立缓冲区：QImage(bytes,...) 只是引用 Python
        # 字节对象的内存，跨线程 emit 前必须脱离这块临时内存。
        img = QImage(bytes(buf), w, h, w * 4, QImage.Format.Format_RGB32).copy()
        return None if img.isNull() else img
    except Exception:  # noqa: BLE001 - 抓图失败就当作没有快照，不致命
        return None


class SnapWorker(QThread):
    """后台周期抓窗线程：captured(QImage, is_fullscreen) 每周期发一帧。"""

    captured = Signal(QImage, bool)

    def __init__(self, hwnd_getter, fs_getter, interval_ms: int = _SNAP_INTERVAL_MS,
                 parent=None):
        super().__init__(parent)
        self._hwnd_getter = hwnd_getter
        self._fs_getter = fs_getter
        self._interval = max(40, int(interval_ms))
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D401 - QThread 入口
        while not self._stop:
            try:
                hwnd = int(self._hwnd_getter() or 0)
                if hwnd:
                    img = _capture_window_qimage(hwnd)
                    if img is not None and not img.isNull():
                        self.captured.emit(img, bool(self._fs_getter()))
            except Exception:  # noqa: BLE001
                pass
            # ⛔ 分段睡眠，不要直接 msleep(interval)：一次睡满 160ms 时 stop()
            #    后仍要等完整个周期，关窗路径的 wait() 可能超时；超时后窗口析构
            #    会连带 delete 这个仍在运行的 QThread → qFatal 静默退出。
            #    切成 10ms 小片后 stop() 最迟 10ms 内生效，wait() 必定能收回。
            for _ in range(max(1, self._interval // 10)):
                if self._stop:
                    break
                self.msleep(10)
