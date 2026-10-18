# -*- coding: utf-8 -*-
r"""像素级闪帧探针：不看几何，直接看**屏幕上每一帧变了多少像素**。

为什么要有它
------------
几何探针（``_probe_full_anim_audit.py``）只能证明"被驱动的量是连续的"。
但"闪"往往是**几何没动、像素却整片变了**的情况：阴影凭空出现、快照被撤、
z-order 换层、一帧硬切。这些只有抓屏比对才能看见。

怎么做
------
· 抓屏走纯 GDI ``BitBlt``（``_desktop_gate`` 同款），**带源偏移**只抓会变的那块
  区域 —— 整屏 1600x1000 每帧 diff 太贵，会把采样间隔拖到 40ms+（memory 里
  QA 就栽在这：260ms 的坡只采到 5~6 帧，台阶看起来像跳变）。
· 判"闪"用**帧间差异点数**：正常动画是一条坡（相邻帧差异量级相近），闪是
  某一帧的差异远大于邻帧、且随后又变回去（revert）。
· 差异统计按步长抽样（每 N 个像素取 1 个），够灵敏又不拖慢采样。

⛔ 桌面活性门禁：开拍前 ``require_alive``；**每个场景开拍前再查一次**，
中途变黑就把该场景标 ``[SKIP]``，⛔ 绝不把黑屏下的"0 差异"写成 PASS。

用法：python devtools/_probe_pixel_flash.py
产物：_check/pixel_flash.txt
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEV = os.path.dirname(os.path.abspath(__file__))
for p in (_ROOT, _DEV):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtWidgets import QApplication                    # noqa: E402

from _desktop_gate import is_alive, require_alive             # noqa: E402

OUT_PATH = os.path.join(_ROOT, "_check", "pixel_flash.txt")
LINES: list[str] = []
STRIDE = 12            # 每 N 个像素取 1 个参与统计（抽样，不是全量）
DELTA_MIN = 10         # 单像素三通道差之和超过这个才算"变了"

SM_CXSCREEN, SM_CYSCREEN = 0, 1
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32


class _BMI(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BMI), ("bmiColors", wintypes.DWORD * 3)]


def grab_rect(x: int, y: int, w: int, h: int) -> bytes:
    """抓屏幕 (x,y,w,h) 一块，返回 BGRA 字节流（自上而下）。"""
    screen = _user32.GetDC(0)
    mem = _gdi32.CreateCompatibleDC(screen)
    bmp = _gdi32.CreateCompatibleBitmap(screen, w, h)
    _gdi32.SelectObject(mem, bmp)
    _gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, SRCCOPY)
    bi = _BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(_BMI)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    _gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)
    _gdi32.DeleteObject(bmp)
    _gdi32.DeleteDC(mem)
    _user32.ReleaseDC(0, screen)
    return buf.raw


def diff_count(a: bytes, b: bytes, stride: int = STRIDE) -> tuple[int, int]:
    """抽样比较两帧：(变化的抽样点数, 单像素最大差)。"""
    step = 4 * stride
    hits = 0
    biggest = 0
    for i in range(0, len(a), step):
        d = (abs(a[i] - b[i]) + abs(a[i + 1] - b[i + 1])
             + abs(a[i + 2] - b[i + 2]))
        if d > DELTA_MIN:
            hits += 1
            if d > biggest:
                biggest = d
    return hits, biggest


def say(line: str = "") -> None:
    print(line)
    LINES.append(line)


class Capture:
    """一边泵事件循环一边抓帧。"""

    def __init__(self, app, rect):
        self.app = app
        self.rect = rect

    def run(self, action, seconds: float = 1.4):
        frames: list[tuple[float, bytes]] = []
        t0 = time.perf_counter()
        action()
        while time.perf_counter() - t0 < seconds:
            self.app.processEvents()
            frames.append(((time.perf_counter() - t0) * 1000.0,
                           grab_rect(*self.rect)))
        return frames


def analyse(name: str, frames, region_note: str = "") -> None:
    say("-" * 76)
    say(f"【{name}】{('  ' + region_note) if region_note else ''}")
    if len(frames) < 3:
        say(f"  [SKIP] 只抓到 {len(frames)} 帧，样本不足（不做任何判定）")
        return
    dts = [b[0] - a[0] for a, b in zip(frames, frames[1:])]
    say(f"  抓到 {len(frames)} 帧，采样间隔 中位 "
        f"{sorted(dts)[len(dts)//2]:.1f}ms / 最大 {max(dts):.0f}ms"
        f"（抓屏是同步 BitBlt，间隔粗是已知的）")
    diffs = []
    for a, b in zip(frames, frames[1:]):
        hits, big = diff_count(a[1], b[1])
        diffs.append((b[0], hits, big))
    series = [d[1] for d in diffs]
    moved = [v for v in series if v > 0]
    if not moved:
        say("  ⚠️ 全程 0 差异 —— 要么这块区域真的没动（探针选区错了），"
            "要么桌面黑了。检查上面的采样间隔与下面的 luma 复核。")
        return
    srt = sorted(moved)
    med = srt[len(srt) // 2]
    top = max(diffs, key=lambda d: d[1])
    say(f"  帧间差异点数：中位 {med} / 最大 {top[1]}（@"
        f"{top[0]:.0f}ms，单像素最大差 {top[2]}）/ 变化帧 "
        f"{len(moved)}/{len(series)}")
    say("  差异序列: " + " ".join(str(v) for v in series[:40]))
    flags = []
    if med and top[1] > max(3 * med, med + 400):
        flags.append(f"F1 差异尖峰 {top[1]}@"
                     f"{top[0]:.0f}ms = 中位的 {top[1]/max(med,1):.1f}×")
    # revert：尖峰的下一帧变化量也很大（内容又变回去 = 闪的典型签名）
    idx = diffs.index(top)
    if idx + 1 < len(diffs) and diffs[idx + 1][1] > max(2 * med, 200):
        flags.append(f"F2 尖峰后紧接着又大变 {diffs[idx+1][1]}@"
                     f"{diffs[idx+1][0]:.0f}ms（疑似一帧闪回）")
    if idx > 0 and diffs[idx - 1][1] > max(2 * med, 200):
        flags.append(f"F3 尖峰前一帧也大变 {diffs[idx-1][1]}（尖峰不孤立）")
    # 首尾残差（动画前后是否真的回到同一画面）
    first_last, _ = diff_count(frames[0][1], frames[-1][1])
    say(f"  首尾残差（开始 vs 落定）= {first_last} 点")
    say(f"  判据: {'；'.join(flags) if flags else 'OK（没有脱离中位的尖峰）'}")


def main() -> int:
    require_alive("pixel_flash")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    from PySide6.QtCore import QSettings
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    from ui_theme import apply_scheme, ui_font
    apply_scheme("dark")
    app.setFont(ui_font(12.5))
    from ui_anim import display_max_hz, set_animations_enabled_override
    from ui_anim import set_frame_hz
    set_animations_enabled_override(True)
    set_frame_hz(display_max_hz())

    for path in list(sys.path):
        if os.path.basename(path.rstrip("\\/")).lower() == "_check":
            sys.path.remove(path)

    from main_window import MainWindow
    window = MainWindow()
    window._port_watch.stop()
    window.show()
    window.apply_startup_state()
    end = time.perf_counter() + 0.8
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.003)
    window.raise_()
    app.processEvents()
    time.sleep(0.15)
    app.processEvents()

    say("=" * 76)
    say("像素级闪帧探针（BitBlt 抓屏 + 帧间差异点数）  桌面 = ALIVE")
    say("每个场景开拍前复检桌面活性；中途变黑则该场景标 [SKIP]，不记 PASS")
    say("=" * 76)

    try:
        # 场景选区：只抓会变的那一块，采样才够密
        g = window.geometry()

        def rect_center(cx, cy, w, h):
            return (max(0, int(cx - w / 2)), max(0, int(cy - h / 2)), int(w), int(h))

        cap_settings = Capture(app, rect_center(g.center().x(), g.center().y(),
                                                620, 780))

        def _open_settings():
            window.btn_settings.click()

        def _close_settings():
            window._close_settings_dialog(False)

        if not is_alive():
            say("[SKIP] 设置面板入场：桌面已黑")
        else:
            analyse("设置面板入场（含落定换帧那一下）",
                    cap_settings.run(_open_settings, 1.5),
                    f"选区 {cap_settings.rect}")
        # ⚠️ 面板必须关掉再往下走：它盖在窗口中间，后面几个场景的选区会被它压住
        #    （第一版探针就是漏了这一步，发送区/弹层/搜索条全部量到"0 差异"
        #     —— 那不是"没有闪"，是压根没动）
        end = time.perf_counter() + 0.6
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.003)
        if is_alive():
            analyse("设置面板收场", cap_settings.run(_close_settings, 1.2))
        end = time.perf_counter() + 0.6
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.003)
        say(f"  （面板已关：_settings_dialog="
            f"{getattr(window, '_settings_dialog', None)}）")

        # 接收区最大化 / 退出：整条窗口高度都会变，抓左半屏够用
        cap_rx = Capture(app, (0, 0, int(g.width() * 0.75), int(g.height())))
        if not is_alive():
            say("[SKIP] 接收区最大化：桌面已黑")
        else:
            analyse("接收区最大化", cap_rx.run(lambda: window.btn_expand.click(),
                                               1.4), f"选区 {cap_rx.rect}")
        end = time.perf_counter() + 0.6
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.003)
        if not is_alive():
            say("[SKIP] 退出最大化：桌面已黑")
        else:
            analyse("退出最大化", cap_rx.run(lambda: window.btn_expand.click(),
                                             1.4))
        end = time.perf_counter() + 0.6
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.003)

        # 发送区折叠/展开：只抓下半屏（发送区 + 接收区下沿）
        # 选区直接用**发送卡自己的屏幕矩形**（外扩 40px 留阴影），别靠猜
        txg = window._tx_card.geometry()
        tp = window._tx_card.mapToGlobal(txg.topLeft())
        low = (max(0, tp.x() - 40), max(0, tp.y() - 40),
               min(int(g.width()) - max(0, tp.x() - 40), txg.width() + 80),
               min(int(g.height()) - max(0, tp.y() - 40), txg.height() + 120))
        cap_tx = Capture(app, low)
        if is_alive():
            before = window._tx_collapsed
            rows = cap_tx.run(lambda: window.btn_minimize.click(), 1.3)
            say(f"  （折叠状态 {before} → {window._tx_collapsed}，"
                f"发送卡屏幕矩形 {tp.x()},{tp.y()} {txg.width()}x{txg.height()}）")
            analyse("发送区折叠", rows, f"选区 {low}")
        else:
            say("[SKIP] 发送区折叠：桌面已黑")
        end = time.perf_counter() + 0.5
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.003)
        if is_alive():
            before = window._tx_collapsed
            rows = cap_tx.run(lambda: window.btn_minimize.click(), 1.3)
            say(f"  （折叠状态 {before} → {window._tx_collapsed}）")
            analyse("发送区展开", rows)
        else:
            say("[SKIP] 发送区展开：桌面已黑")
        end = time.perf_counter() + 0.5
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.003)

        # 下拉弹层：抓波特率那颗下拉周围
        combo = window.cmb_baud
        pos = combo.mapToGlobal(combo.rect().topLeft())
        cap_pop = Capture(app, (max(0, pos.x() - 20), max(0, pos.y() - 10),
                                360, 420))
        if is_alive():
            analyse("下拉弹层展开", cap_pop.run(combo.showPopup, 0.9),
                    f"选区 {cap_pop.rect}")
            combo.hidePopup()
        else:
            say("[SKIP] 下拉弹层：桌面已黑")
        end = time.perf_counter() + 0.4
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.003)

        # 搜索条：抓接收区上半
        cap_s = Capture(app, (0, 0, int(g.width() * 0.75), 260))
        if is_alive():
            analyse("搜索条展开", cap_s.run(lambda: window.btn_search.click(),
                                            1.0))
            end = time.perf_counter() + 0.4
            while time.perf_counter() < end:
                app.processEvents()
                time.sleep(0.003)
            analyse("搜索条收起", cap_s.run(lambda: window.btn_search.click(),
                                            1.0))
        else:
            say("[SKIP] 搜索条：桌面已黑")

        # 收尾复检：整轮跑完桌面还活不活（不活的话前面所有数字都不可信）
        say("-" * 76)
        say(f"收尾桌面复检: {'ALIVE' if is_alive() else 'BLACK'}"
            + ("" if is_alive() else
               "  ⛔ 中途黑过 → 上面所有场景的数字都不作数"))
    finally:
        try:
            window.setGeometry(0, 0, 1134, 820)
            window.close()
        except RuntimeError:
            pass
        with open(OUT_PATH, "w", encoding="utf-8") as fh:
            fh.write("\n".join(LINES) + "\n")
        say(f"\n逐帧数据 → {OUT_PATH}")
        for key in list(settings.allKeys()):
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            if value is not None:
                settings.setValue(key, value)
        settings.sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
