# -*- coding: utf-8 -*-
"""win-cover-3：最大化/还原窗口动画**逐帧取证**驱动器（只读产品源码，探针不改产品）。

与旧版 _winfx_drive.py 的差异：
  1. 录制改用 ddagrab **165fps**（= 本机刷新率，1 帧 = 1 次刷新，不做跳帧），
     这样「重复帧/保持帧长度」能直接反映 60fps 封顶在 165Hz 屏上的阶梯。
  2. 录屏分辨率 1600x1000（= 逻辑分辨率，dpr 已归一），逐帧 diff 阈值按此标定。
  3. 背景用**满屏洋红色背影窗**垫底（挡住 WorkBuddy 等无关窗口的闪动），
     洋红只存在于测试期间；这样"覆盖层透出背景"（alpha 漏洞）在亮度+色度上
     一眼可辨（应用是黑灰 UI，260° 洋红）。
  4. 遥测只做记录：toggle / cover_begin / cover_run / tick / cover_finish /
     showFullScreen / showNormal / setWindowOpacity / 5ms 状态采样，全部带
     perf_counter + time.time 双时钟，供与视频 pts 对齐。

产物（_check/）：
  _c3_<tag>.jsonl            事件遥测
  _c3_<tag>_samples.jsonl    5ms 状态采样
  _c3_<tag>.mkv              165fps 录屏
  _c3_<tag>.ffmpeg.log       showinfo（每帧 pts_time）

用法（独占运行，自带 _check/.app_run.lock 互斥）：
  QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_c3_winfx_record.py \
      --cycles 6 --warmup 2 --tag c3
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import gc
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "_check"
LOCK = CHECK / ".app_run.lock"
sys.path.insert(0, str(ROOT))

LOG: list[dict] = []
EDGE: list[dict] = []
SAMPLES: list[dict] = []
T0PC = time.perf_counter()
T0WALL = time.time()

# ---------------------------------------------------------------- 屏幕取样条
# 逐帧录屏最高只到 ~100fps（ddagrab+hwdownload 带宽上限），看不清 165Hz 的
# "保持 2 帧 / 3 帧"阶梯。这里用 GDI BitBlt 对**屏幕正中一条 2x2000 竖条**
# 高频取样（~0.1ms/次），取"最上面一行不是背景色"= 窗口顶边 y。分辨率 ~0.2ms。
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_user32.GetDC.restype = wt.HDC
_user32.GetDC.argtypes = [wt.HWND]
_gdi32.CreateCompatibleDC.restype = wt.HDC
_gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
_gdi32.BitBlt.restype = wt.BOOL
_gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int,
                          wt.DWORD]
_gdi32.SelectObject.restype = wt.HANDLE
_gdi32.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
_gdi32.DeleteObject.argtypes = [wt.HANDLE]
_gdi32.DeleteDC.argtypes = [wt.HDC]
_user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class _BI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BIH), ("bmiColors", wt.DWORD * 3)]


class EdgeTracker(threading.Thread):
    """对屏幕竖条高频取样，记录"窗口顶边 y（物理像素）"随 wall 时间的变化。"""

    def __init__(self, x_phys: int, y0: int, width: int = 2, height: int = 2000):
        super().__init__(daemon=True)
        self.x, self.y0, self.w, self.h = x_phys, y0, width, height
        self._stop_evt = threading.Event()
        self.rows: list[dict] = []
        self._hs = _user32.GetDC(None)
        self._hm = _gdi32.CreateCompatibleDC(self._hs)
        bi = _BI()
        bi.bmiHeader.biSize = ctypes.sizeof(_BIH)
        bi.bmiHeader.biWidth = width
        bi.bmiHeader.biHeight = -height
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        _gdi32.CreateDIBSection.restype = wt.HANDLE
        _gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                            ctypes.POINTER(ctypes.c_void_p),
                                            wt.HANDLE, wt.DWORD]
        self._hb = _gdi32.CreateDIBSection(self._hs, ctypes.byref(bi), 0,
                                           ctypes.byref(bits), None, 0)
        _gdi32.SelectObject(self._hm, self._hb)
        self._buf = (ctypes.c_ubyte * (width * height * 4)).from_address(bits.value)

    def stop(self):
        self._stop_evt.set()

    def run(self):
        import numpy as np
        buf = self._buf
        w = self.w
        h = self.h
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        while not self._stop_evt.is_set():
            t = time.perf_counter()
            _gdi32.BitBlt(self._hm, 0, 0, w, h, self._hs, self.x, self.y0, 0x00CC0020)
            r = arr[:, :, 2].astype(np.int16)
            g = arr[:, :, 1].astype(np.int16)
            b = arr[:, :, 0].astype(np.int16)
            is_bg = ((r - g) > 60) & ((b - g) > 60)
            rows_all_bg = is_bg.all(axis=1)
            hit = np.nonzero(~rows_all_bg)[0]
            top = int(hit[0]) if hit.size else -1
            self.rows.append({"pc": (t - T0PC) * 1000.0,
                              "wall": t - T0WALL,
                              "y": top,
                              "dur_us": (time.perf_counter() - t) * 1e6})
            # 自适应休眠：留 1ms 给主线程，避免抢 GIL 拖慢动画
            time.sleep(0.0008)

    def close(self):
        try:
            _gdi32.DeleteObject(self._hb)
            _gdi32.DeleteDC(self._hm)
            _user32.ReleaseDC(None, self._hs)
        except Exception:                              # noqa: BLE001
            pass


def ev(name: str, **kw) -> None:
    rec = {"pc": round((time.perf_counter() - T0PC) * 1000.0, 3),
           "wall": round(time.time() - T0WALL, 4), "ev": name}
    rec.update({k: (round(v, 3) if isinstance(v, float) else v)
                for k, v in kw.items()})
    LOG.append(rec)


def acquire_lock(max_wait: float = 240.0) -> None:
    t0 = time.time()
    while True:
        try:
            LOCK.mkdir()
            (LOCK / "pid").write_text(str(os.getpid()), encoding="utf-8")
            return
        except FileExistsError:
            if time.time() - t0 > max_wait:
                raise SystemExit(f"锁被占用超过 {max_wait}s：{LOCK}")
            time.sleep(0.5)


def release_lock() -> None:
    try:
        for p in LOCK.glob("*"):
            p.unlink()
        LOCK.rmdir()
    except OSError:
        pass


def dump_settings(settings) -> dict:
    return {k: settings.value(k) for k in settings.allKeys()}


def restore_settings(settings, snap: dict) -> list[str]:
    now = set(settings.allKeys())
    extra = sorted(now - set(snap))
    for key in extra:
        settings.remove(key)
    for key, val in snap.items():
        settings.setValue(key, val)
    settings.sync()
    return extra


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=6, help="录制期内进/出各 cycles 次")
    ap.add_argument("--warmup", type=int, default=2, help="录制前预热 cycle 数")
    ap.add_argument("--tag", default="c3")
    ap.add_argument("--interval", type=float, default=1.65, help="两次 toggle 间隔秒")
    ap.add_argument("--no-video", action="store_true")
    args = ap.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    CHECK.mkdir(exist_ok=True)
    acquire_lock()
    ev("t0pc_abs", v=T0PC)

    jsonl_path = CHECK / f"_c3_{args.tag}.jsonl"
    samp_path = CHECK / f"_c3_{args.tag}_samples.jsonl"
    mkv_path = CHECK / f"_c3_{args.tag}.mkv"
    log_path = CHECK / f"_c3_{args.tag}.ffmpeg.log"

    from PySide6.QtCore import QSettings, QRect, QTimer
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication, QWidget

    import ui_anim
    qsettings = QSettings("MimoWorks", "SerialAssistant")
    snap = dump_settings(qsettings)
    print(f"QSettings 快照 {len(snap)} 键")

    app = QApplication(sys.argv[:1])
    app.setApplicationName("SerialAssistant")
    app.setOrganizationName("MimoWorks")

    import customization
    applied_hz = ui_anim.set_frame_hz(int(customization.get("anim_fps")))
    print(f"frame_hz={ui_anim.frame_hz()} display_max_hz={ui_anim.display_max_hz()} "
          f"applied={applied_hz}")

    from main_window import MainWindow

    scr = app.primaryScreen()
    sg = scr.geometry()
    dpr = scr.devicePixelRatio()
    print(f"screen={sg.width()}x{sg.height()} dpr={dpr} refresh={scr.refreshRate():.1f}")

    # ---- 洋红背影窗：挡住无关窗口，同时充当"透底"探针 ----
    from PySide6.QtCore import Qt
    backdrop = QWidget(None, Qt.Window | Qt.FramelessWindowHint
                       | Qt.WindowStaysOnTopHint | Qt.Tool)
    backdrop.setAttribute(Qt.WA_ShowWithoutActivating, True)
    backdrop.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    backdrop.setAutoFillBackground(True)
    pal = backdrop.palette()
    pal.setColor(backdrop.backgroundRole(), QColor(190, 0, 190))
    backdrop.setPalette(pal)
    backdrop.setGeometry(sg)
    backdrop.show()
    app.processEvents()

    win = MainWindow()
    win._skip_startup_anim = False
    win._startup_phase = False
    try:
        win._port_watch.stop()
    except Exception:                                  # noqa: BLE001
        pass

    # ---------------- 内部遥测（只记录，不改行为） ----------------
    def wrap(name, hook):
        orig = getattr(win, name, None)
        if orig is None:
            print(f"!! 缺方法 {name}")
            return

        def inner(*a, **kw):
            return hook(orig, a, kw)
        setattr(win, name, inner)

    def geo_of(w):
        g = w.geometry()
        return [g.x(), g.y(), g.width(), g.height()]

    def h_begin(orig, a, kw):
        ev("cover_begin_in", with_bg=kw.get("with_bg", True),
           pixmap_b="none" if kw.get("pixmap_b") is None else "has",
           start=[a[1].x(), a[1].y(), a[1].width(), a[1].height()],
           win_op=round(win.windowOpacity(), 4), is_fs=win.isFullScreen())
        t0 = time.perf_counter()
        r = orig(*a, **kw)
        ev("cover_begin_out", ok=r is not None,
           dur_ms=(time.perf_counter() - t0) * 1e3,
           has_bg=win._cover_bg is not None)
        return r

    def h_run(orig, a, kw):
        dur = a[3] if len(a) > 3 else kw.get("duration")
        r = orig(*a, **kw)
        ev("cover_run",
           start=[a[1].x(), a[1].y(), a[1].width(), a[1].height()],
           end=[a[2].x(), a[2].y(), a[2].width(), a[2].height()],
           duration=dur, timer_ms=win._cover_timer.interval(),
           frame_hz=ui_anim.frame_hz(), win_op=round(win.windowOpacity(), 4),
           is_fs=win.isFullScreen())
        return r

    def h_tick(orig, a, kw):
        el = win._cover_clock.elapsed() if hasattr(win, "_cover_clock") else -1
        t0 = time.perf_counter()
        r = orig(*a, **kw)
        dur_ms = (time.perf_counter() - t0) * 1e3
        lab = win._cover_label
        blab = win._cover_label_b
        bg = win._cover_bg
        ev("tick", el=el,
           t=round(min(1.0, el / float(max(1, win._cover_duration))), 4),
           dur_ms=round(dur_ms, 3),
           geo=geo_of(lab) if lab is not None else None,
           op=round(lab.windowOpacity(), 4) if lab is not None else None,
           op_b=round(blab.windowOpacity(), 4) if blab is not None else None,
           bg_geo=geo_of(bg) if bg is not None else None,
           bg_op=round(bg.windowOpacity(), 4) if bg is not None else None,
           win_op=round(win.windowOpacity(), 4),
           is_fs=win.isFullScreen(), win_geo=geo_of(win))
        return r

    def h_finish(orig, a, kw):
        ev("cover_finish_in", from_tick=kw.get("from_tick", a[0] if a else None),
           win_op=round(win.windowOpacity(), 4), is_fs=win.isFullScreen())
        r = orig(*a, **kw)
        ev("cover_finish_out", win_op=round(win.windowOpacity(), 4),
           is_fs=win.isFullScreen(), geo=geo_of(win))
        return r

    def h_setopacity(orig, a, kw):
        val = a[0] if a else kw.get("value")
        ev("setWindowOpacity_in", v=float(val),
           is_fs=win.isFullScreen(), geo=geo_of(win))
        r = orig(*a, **kw)
        ev("setWindowOpacity_out", v=float(val), real=round(win.windowOpacity(), 4))
        return r

    def h_showfs(orig, a, kw):
        ev("showFullScreen_in", win_op=round(win.windowOpacity(), 4), geo=geo_of(win))
        r = orig(*a, **kw)
        ev("showFullScreen_out", is_fs=win.isFullScreen(), geo=geo_of(win))
        return r

    def h_shownormal(orig, a, kw):
        ev("showNormal_in", win_op=round(win.windowOpacity(), 4), geo=geo_of(win))
        r = orig(*a, **kw)
        ev("showNormal_out", is_fs=win.isFullScreen(), geo=geo_of(win))
        return r

    for nm, hk in (("_begin_window_cover", h_begin),
                   ("_run_window_cover", h_run),
                   ("_on_cover_tick", h_tick),
                   ("_finish_window_cover", h_finish),
                   ("setWindowOpacity", h_setopacity),
                   ("showFullScreen", h_showfs),
                   ("showNormal", h_shownormal)):
        wrap(nm, hk)

    def sampler() -> None:
        g = win.geometry()
        lab = win._cover_label
        blab = win._cover_label_b
        bg = win._cover_bg
        SAMPLES.append({
            "pc": round((time.perf_counter() - T0PC) * 1000.0, 3),
            "wall": round(time.time() - T0WALL, 4),
            "win_op": round(win.windowOpacity(), 4),
            "fs": win.isFullScreen(),
            "geo": [g.x(), g.y(), g.width(), g.height()],
            "cover": lab is not None,
            "cover_geo": geo_of(lab) if lab is not None else None,
            "cover_op": round(lab.windowOpacity(), 4) if lab is not None else None,
            "cover_op_b": round(blab.windowOpacity(), 4) if blab is not None else None,
            "bg": bg is not None,
            "bg_geo": geo_of(bg) if bg is not None else None,
            "bg_op": round(bg.windowOpacity(), 4) if bg is not None else None,
        })

    # ---- 起手：普通窗口（1134x820 居中），不走进全屏 ----
    w, h = 1134, 820
    win.setWindowFlag(Qt.WindowStaysOnTopHint, True)   # 与背影窗同带，最后 raise 的在上
    win.setGeometry(sg.x() + (sg.width() - w) // 2,
                    sg.y() + max(40, (sg.height() - h) // 2), w, h)
    win.setWindowOpacity(1.0)
    win.show()
    win.raise_()
    win.activateWindow()
    app.processEvents()
    win._start_snap_worker()
    try:
        win._remember_normal_geometry()
    except Exception:                                  # noqa: BLE001
        pass
    ev("shown_normal", geo=geo_of(win), normal=getattr(win, "_normal_geometry", None)
       and [win._normal_geometry.x(), win._normal_geometry.y(),
            win._normal_geometry.width(), win._normal_geometry.height()])

    samp_timer = QTimer(win)
    samp_timer.setInterval(5)
    samp_timer.timeout.connect(sampler)
    samp_timer.start()

    # 屏幕正中竖条取样（独立进程，避免 GIL 饥饿与扰动）
    edge_out = CHECK / f"_c3_{args.tag}_edge.jsonl"
    edge_proc = subprocess.Popen(
        [sys.executable, str(ROOT / "devtools" / "_c3_edge_probe.py"),
         "--x", str(sg.width() // 2), "--y", "0", "--w", "2",
         "--h", "700",
         "--dur", "70", "--out", str(edge_out)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    state = {"phase": 0, "toggles": 0, "rec": False, "ffmpeg": None}
    total_record = args.cycles * 2
    total_all = (args.warmup + args.cycles) * 2

    def start_recording() -> None:
        if args.no_video:
            state["rec"] = True
            ev("record_ready")
            return
        dur = 3 + total_record * args.interval + 3
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
            "-f", "lavfi", "-i",
            "ddagrab=output_idx=0:framerate=165:draw_mouse=0",
            "-vf", f"hwdownload,format=bgra,scale={sg.width()}:{sg.height()}"
                   ":flags=fast_bilinear,showinfo",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
            "-t", str(dur), str(mkv_path),
        ]
        ev("ffmpeg_start", cmd=" ".join(cmd))
        state["ffmpeg"] = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL,
            stderr=open(log_path, "w", encoding="utf-8", errors="replace"))
        time.sleep(1.5)
        state["rec"] = True
        ev("record_ready")

    def do_toggle() -> None:
        if state["toggles"] >= total_all:
            ev("all_done")
            QTimer.singleShot(700, app.quit)
            return
        if args.warmup * 2 <= state["toggles"] < args.warmup * 2 + total_record:
            idx = state["toggles"] - args.warmup * 2
            recording = True
        else:
            idx = -1
            recording = False
        was_fs = win.isFullScreen()
        direction = "restore" if was_fs else "enter"
        rec_no = (idx // 2 + 1) if idx >= 0 else 0
        ev("toggle", idx=idx, rec=recording, cycle=rec_no, dir=direction,
           is_fs=was_fs, win_op=round(win.windowOpacity(), 4), geo=geo_of(win))
        win.activateWindow()
        win.raise_()
        win._toggle_fullscreen()
        state["toggles"] += 1
        QTimer.singleShot(int(args.interval * 1000), do_toggle)

    def start_timer() -> None:
        start_recording()
        QTimer.singleShot(300, do_toggle)

    QTimer.singleShot(4500, start_timer)   # 等 SnapWorker + 首帧稳定

    t_enter = time.time()
    app.exec()
    samp_timer.stop()
    print(f"事件循环退出（{time.time() - t_enter:.1f}s）")

    ff = state["ffmpeg"]
    if ff is not None:
        try:
            ff.wait(timeout=10)
        except subprocess.TimeoutExpired:
            ff.terminate()
            ff.wait(timeout=5)

    try:
        win._stop_snap_worker()
    except Exception:                                  # noqa: BLE001
        pass
    try:
        win.close()
    except Exception:                                  # noqa: BLE001
        pass
    try:
        win.deleteLater()
        backdrop.deleteLater()
    except Exception:                                  # noqa: BLE001
        pass
    app.processEvents()
    del win
    gc.collect()

    ev("end", pc=round((time.perf_counter() - T0PC) * 1000.0, 3))
    jsonl_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in LOG) + "\n",
        encoding="utf-8")
    samp_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in SAMPLES) + "\n",
        encoding="utf-8")
    try:
        edge_proc.terminate()
        edge_proc.wait(timeout=10)
    except Exception:                                  # noqa: BLE001
        pass

    extra = restore_settings(qsettings, snap)
    del qsettings
    gc.collect()
    q2 = QSettings("MimoWorks", "SerialAssistant")
    after = dump_settings(q2)
    bad = [k for k in snap if after.get(k) != snap[k]]
    print(f"设置还原：删除多余键 {extra}；不一致键 {bad}")
    print(f"遥测 {len(LOG)} 行 / 采样 {len(SAMPLES)} 行 → {jsonl_path.name}")
    release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
