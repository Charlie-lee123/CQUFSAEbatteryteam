# -*- coding: utf-8 -*-
r"""开屏收尾闪烁诊断 —— 录制器（只读，不 import 任何产品源码模块）。

针对**已部署的** 发布/SerialAssistant.exe（2026-11-06 20:12 构建、旧版）录一遍
完整开屏收尾。工程师正在并行改 splash_anim.py / splash_motion.py，所以本探针
绝不 import 源码、绝不跑真实主窗口 —— 画面全部来自 exe 自己在屏上的合成结果。

两路证据：
  1. ffmpeg ddagrab（DWM Desktop Duplication，165fps）录全屏合成 →
     _check/_tailflash_raw.mkv + _tailflash_ffmpeg.log（showinfo 每帧 pts_time）。
     ddagrab 抓的是 DWM 合成后画面，能抓到 translucent/layered 窗口与撤场重合成。
  2. GDI BitBlt 高频（~0.5ms）中心 ROI 亮度采样 → _check/_tailflash_luma.jsonl，
     给"闪"一个不受录屏帧率限制的精确时间戳/持续时间。

安全：
  · winreg 递归全量快照 HKCU\Software\MimoWorks\SerialAssistant，finally 原样还原
    （新增值/子键删除，原值按原 REG type 写回），exe 退出后才还原，二次校验。
  · _check/.app_run.lock 互斥 + 检查无残留 SerialAssistant.exe 进程，禁并发。
  · 桌面活性门禁（GetDC(0)+BitBlt 非全黑）不通过直接 SystemExit(2)。
  · ⛔ 不改产品源码、不 git、不打包、不动 发布/ 与 dist*，产物全部落 _check/。

用法（独占）：
  QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_probe_splash_tail_flash.py
"""
from __future__ import annotations

import ctypes
import argparse
import ctypes.wintypes as wt
import json
import os
import re
import subprocess
import sys
import threading
import time
import winreg
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "_check"
EXE = ROOT / "发布" / "SerialAssistant.exe"
LOCK = CHECK / ".app_run.lock"
MKV = CHECK / "_tailflash_raw.mkv"
FFLOG = CHECK / "_tailflash_ffmpeg.log"
LUMA = CHECK / "_tailflash_luma.jsonl"
META = CHECK / "_tailflash_meta.json"

QKEY = r"Software\MimoWorks\SerialAssistant"
DUR = 9.0                 # 录制时长（覆盖 起播预热 + 几何 3.2s + 淡出 0.3s + 余波）
FF_SETTLE = 1.5          # 等 ddagrab 就绪
REC_W, REC_H = 1600, 1068   # 逻辑分辨率（物理 3200x2136，dpr=2）

T0 = time.perf_counter()
EVENTS: list[dict] = []


def pc_ms() -> float:
    return round((time.perf_counter() - T0) * 1000.0, 3)


def ev(name: str, **kw) -> None:
    r = {"pc_ms": pc_ms(), "ev": name}
    r.update(kw)
    EVENTS.append(r)
    print(f"[ev] {r['pc_ms']:9.1f}ms  {name}  {kw}", flush=True)


# ------------------------------------------------------------------ QSettings 快照
def _enum_all(root: str) -> dict:
    """递归快照：{子路径: {值名: (REG_type, data)}}。"""
    out: dict = {}

    def walk(path: str) -> None:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ)
        vals = {}
        i = 0
        while True:
            try:
                n, v, t = winreg.EnumValue(k, i)
            except OSError:
                break
            vals[n] = (t, v)
            i += 1
        subs = []
        j = 0
        while True:
            try:
                subs.append(winreg.EnumKey(k, j))
            except OSError:
                break
            j += 1
        winreg.CloseKey(k)
        out[path] = vals
        for s in subs:
            walk(path + "\\" + s)

    walk(root)
    return out


def _all_paths(root: str) -> list[str]:
    paths = []

    def walk(path: str) -> None:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ)
        subs = []
        j = 0
        while True:
            try:
                subs.append(winreg.EnumKey(k, j))
            except OSError:
                break
            j += 1
        winreg.CloseKey(k)
        paths.append(path)
        for s in subs:
            walk(path + "\\" + s)

    try:
        walk(root)
    except OSError:
        pass
    return paths


def restore_settings(snap: dict) -> dict:
    """删新增、按原 REG type 写回原值。返回 {added_values, added_keys, bad}。"""
    added_values, added_keys, bad = [], [], []
    now_paths = _all_paths(QKEY)
    # 1) 删不在快照里的值
    for p in now_paths:
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, p, 0, winreg.KEY_READ)
        except OSError:
            continue
        names = []
        i = 0
        while True:
            try:
                names.append(winreg.EnumValue(k, i)[0])
            except OSError:
                break
            i += 1
        winreg.CloseKey(k)
        want = set(snap.get(p, {}))
        for n in names:
            if n not in want:
                kw = winreg.OpenKey(winreg.HKEY_CURRENT_USER, p, 0, winreg.KEY_WRITE)
                winreg.DeleteValue(kw, n)
                winreg.CloseKey(kw)
                added_values.append(f"{p}\\{n}")
    # 2) 删不在快照里的子键（自最深向上）
    for p in sorted(now_paths, key=lambda x: x.count("\\"), reverse=True):
        if p not in snap:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, p)
                added_keys.append(p)
            except OSError:
                pass
    # 3) 原样写回
    for p, vals in snap.items():
        kw = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, p, 0, winreg.KEY_WRITE)
        for n, (t, v) in vals.items():
            try:
                winreg.SetValueEx(kw, n, 0, t, v)
            except (OSError, TypeError, ValueError) as exc:
                bad.append(f"{p}\\{n}:{exc}")
        winreg.CloseKey(kw)
    # 4) 二次校验
    after = _enum_all(QKEY)
    for p, vals in snap.items():
        for n, (t, v) in vals.items():
            if after.get(p, {}).get(n) != (t, v):
                bad.append(f"MISMATCH {p}\\{n}")
    return {"added_values": added_values, "added_keys": added_keys, "bad": bad}


# ------------------------------------------------------------------ 桌面活性门禁
_u = ctypes.WinDLL("user32", use_last_error=True)
_g = ctypes.WinDLL("gdi32", use_last_error=True)
_u.GetDC.restype = wt.HDC
_u.GetDC.argtypes = [wt.HWND]
_u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
_g.CreateCompatibleDC.restype = wt.HDC
_g.CreateCompatibleDC.argtypes = [wt.HDC]
_g.CreateCompatibleBitmap.restype = wt.HANDLE
_g.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
_g.BitBlt.restype = wt.BOOL
_g.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                      ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
_g.SelectObject.restype = wt.HANDLE
_g.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
_g.DeleteObject.argtypes = [wt.HANDLE]
_g.DeleteDC.argtypes = [wt.HDC]
SRCCOPY = 0x00CC0020


def desktop_gate() -> None:
    """GetDC(0)+BitBlt 抓一整屏，纯黑则拒绝出任何结论（同 _desktop_gate 口径）。"""
    w = int(_u.GetSystemMetrics(0))
    h = int(_u.GetSystemMetrics(1))
    hs = _u.GetDC(None)
    hm = _g.CreateCompatibleDC(hs)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    class BI(ctypes.Structure):
        _fields_ = [("bmiHeader", BIH), ("bmiColors", wt.DWORD * 3)]

    # 用 CreateDIBSection 直接拿 32bpp 像素指针，避开 GetDIBits 的 HBITMAP 句柄
    # 在 64 位 Python 下被 ctypes 默认当 c_int 截断的坑。
    bi = BI()
    bi.bmiHeader.biSize = ctypes.sizeof(BIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bits = ctypes.c_void_p()
    _g.CreateDIBSection.restype = wt.HANDLE
    _g.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                    ctypes.POINTER(ctypes.c_void_p),
                                    wt.HANDLE, wt.DWORD]
    hb = _g.CreateDIBSection(hs, ctypes.byref(bi), 0,
                             ctypes.byref(bits), None, 0)
    _g.SelectObject(hm, hb)
    _g.BitBlt(hm, 0, 0, w, h, hs, 0, 0, SRCCOPY)
    buf = (ctypes.c_ubyte * (w * h * 4)).from_address(bits.value)
    a = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[::7, ::7, :3].copy()
    _g.DeleteObject(hb)
    _g.DeleteDC(hm)
    _u.ReleaseDC(None, hs)
    luma = a.max(axis=2)
    max_luma = int(luma.max())
    nonzero = float((luma > 8).mean() * 100.0)
    uniq = len(np.unique(a.reshape(-1, 3), axis=0))
    print(f"[gate] 桌面活性 {w}x{h} max_luma={max_luma} "
          f"nonzero={nonzero:.2f}% uniq_colors={uniq}", flush=True)
    if not (max_luma > 24 and nonzero > 5.0 and uniq > 8):
        print("[SKIP] 桌面全黑，像素判据不可信，本次不跑", flush=True)
        raise SystemExit(2)
    ev("desktop_alive", w=w, h=h, max_luma=max_luma,
       nonzero=round(nonzero, 2), uniq=uniq)


# ------------------------------------------------------------------ BitBlt 高频亮度采样
class LumaSampler(threading.Thread):
    """独立线程：~0.5ms 一次 BitBlt 抓中心 ROI，记录亮度/主色时间序列。

    ctypes 调用外部 C 函数会释放 GIL，可与 ffmpeg/exe 并行；numpy 统计持 GIL，
    用下采样（每 8 像素取 1）压到 ~0.2ms 内。
    """

    def __init__(self, x0: int, y0: int, w: int, h: int):
        super().__init__(daemon=True)
        self.x0, self.y0, self.w, self.h = x0, y0, w, h
        self._stop_evt = threading.Event()
        self.rows: list[dict] = []
        self._hs = _u.GetDC(None)
        self._hm = _g.CreateCompatibleDC(self._hs)

        class BIH(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                        ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                        ("biClrImportant", wt.DWORD)]

        class BI(ctypes.Structure):
            _fields_ = [("bmiHeader", BIH), ("bmiColors", wt.DWORD * 3)]

        bi = BI()
        bi.bmiHeader.biSize = ctypes.sizeof(BIH)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        _g.CreateDIBSection.restype = wt.HANDLE
        _g.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                        ctypes.POINTER(ctypes.c_void_p),
                                        wt.HANDLE, wt.DWORD]
        self._hb = _g.CreateDIBSection(self._hs, ctypes.byref(bi), 0,
                                       ctypes.byref(bits), None, 0)
        _g.SelectObject(self._hm, self._hb)
        self._buf = (ctypes.c_ubyte * (w * h * 4)).from_address(bits.value)

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        arr = np.frombuffer(self._buf, dtype=np.uint8).reshape(self.h, self.w, 4)
        sub = arr[::8, ::8, :3]          # 每 8 像素取 1，BGRA→BGR
        prev = None
        while not self._stop_evt.is_set():
            t = pc_ms()
            _g.BitBlt(self._hm, 0, 0, self.w, self.h,
                      self._hs, self.x0, self.y0, SRCCOPY)
            b = float(sub[..., 0].mean())
            g = float(sub[..., 1].mean())
            r = float(sub[..., 2].mean())
            luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
            c = arr[self.h // 2, self.w // 2]
            center = [int(c[2]), int(c[1]), int(c[0])]
            mean = [round(r, 2), round(g, 2), round(b, 2)]
            d = 0.0 if prev is None else abs(r - prev[0]) + abs(g - prev[1]) + abs(b - prev[2])
            prev = (r, g, b)
            self.rows.append({"t": t, "luma": round(luma, 3),
                              "mean_rgb": mean, "center_rgb": center,
                              "d_rgb": round(d, 3)})
            time.sleep(0.0004)

    def close(self) -> None:
        try:
            _g.DeleteObject(self._hb)
            _g.DeleteDC(self._hm)
            _u.ReleaseDC(None, self._hs)
        except Exception:                                   # noqa: BLE001
            pass


# ------------------------------------------------------------------ 锁 / 进程检查
def acquire_lock() -> None:
    try:
        LOCK.mkdir()
        (LOCK / "pid").write_text(str(os.getpid()), encoding="utf-8")
    except FileExistsError as exc:
        raise SystemExit(f"锁被占用（有并发运行）：{LOCK}") from exc


def release_lock() -> None:
    try:
        for p in LOCK.glob("*"):
            p.unlink()
        LOCK.rmdir()
    except OSError:
        pass


def running_exe_pids() -> list[int]:
    r = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq SerialAssistant.exe", "/FO", "CSV", "/NH"],
        capture_output=True)
    # 无匹配进程时 tasklist 会输出中文提示（GBK），不能用默认 utf-8 严格解码
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    pids = []
    for line in out.splitlines():
        m = re.search(r'"SerialAssistant\.exe","(\d+)"', line)
        if m:
            pids.append(int(m.group(1)))
    return pids


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(
        description="开屏收尾录屏（只读探针，不 import 产品源码）")
    ap.add_argument("--tag", default="",
                    help="产物文件名后缀，供新旧版共存（如 od49）；"
                         "留空=旧版默认名 _tailflash_raw.mkv，向后兼容")
    ap.add_argument("--exe", default=str(EXE),
                    help="录制的 exe 路径；默认 发布/SerialAssistant.exe，"
                         "od49 时传 dist_od49/SerialAssistant.exe")
    ap.add_argument("--dur", type=float, default=9.0,
                    help="录制时长（秒）。⛔ onefile 冷启动解包会吃 4~5s，"
                         "9s 可能只录到开屏中段、录不到淡出与主窗（od52 实测），"
                         "冷缓存时给 14")
    args = ap.parse_args()
    # 影子 DUR：main() 内所有用 DUR 的地方都走这个局部值
    DUR = float(args.dur)
    suf = f"_{args.tag}" if args.tag else ""
    # 局部重绑定：带 tag 时输出 _tailflash_<tag>_raw.mkv 等，避免覆盖旧版证据。
    # main() 内这 4 个名字全部走局部值；模块级同名常量保留作默认值/文档。
    tgt_exe = Path(args.exe)
    MKV = CHECK / f"_tailflash{suf}_raw.mkv"
    FFLOG = CHECK / f"_tailflash{suf}_ffmpeg.log"
    LUMA = CHECK / f"_tailflash{suf}_luma.jsonl"
    META = CHECK / f"_tailflash{suf}_meta.json"

    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    CHECK.mkdir(exist_ok=True)

    if not tgt_exe.is_file():
        raise SystemExit(f"部署 exe 不存在：{tgt_exe}")
    exe_mtime = tgt_exe.stat().st_mtime
    ev("exe_info", path=str(tgt_exe), size=tgt_exe.stat().st_size, mtime=exe_mtime)

    pids = running_exe_pids()
    if pids:
        raise SystemExit(f"已有 SerialAssistant.exe 在运行（并发禁止）：{pids}")

    desktop_gate()
    acquire_lock()
    snap = _enum_all(QKEY)
    ev("settings_snapshot", paths=len(snap),
       values=sum(len(v) for v in snap.values()))

    ff = None
    exe = None
    sampler = None
    try:
        # ---- 1) 先起 ffmpeg ddagrab 录制 ----
        for p in (MKV, FFLOG):
            try:
                p.unlink()
            except OSError:
                pass
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
            "-f", "lavfi", "-i",
            "ddagrab=output_idx=0:framerate=165:draw_mouse=0",
            "-vf", f"hwdownload,format=bgra,scale={REC_W}:{REC_H}:"
                   "flags=fast_bilinear,showinfo",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
            "-t", str(DUR), str(MKV),
        ]
        ev("ffmpeg_start", cmd=" ".join(cmd))
        ff = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                              stderr=open(FFLOG, "w", encoding="utf-8",
                                          errors="replace"))
        # 等 ddagrab 真正开始出帧（log 里出现 showinfo 的 pts_time）
        deadline = time.time() + 6.0
        while time.time() < deadline:
            try:
                if "pts_time" in FFLOG.read_text(encoding="utf-8", errors="replace"):
                    break
            except OSError:
                pass
            time.sleep(0.1)
        ev("ffmpeg_ready")

        # ---- 2) BitBlt 亮度采样（物理坐标中心 512x512） ----
        pw = int(_u.GetSystemMetrics(0))
        ph = int(_u.GetSystemMetrics(1))
        rw, rh = 512, 512
        sampler = LumaSampler((pw - rw) // 2, (ph - rh) // 2, rw, rh)
        sampler.start()
        ev("sampler_start", roi=[sampler.x0, sampler.y0, rw, rh], screen=[pw, ph])

        # ---- 3) 启动已部署 exe ----
        exe = subprocess.Popen([str(tgt_exe)], cwd=str(tgt_exe.parent),
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
        ev("exe_launch", pid=exe.pid)

        # ---- 4) 等录制结束 ----
        try:
            ff.wait(timeout=DUR + 8)
        except subprocess.TimeoutExpired:
            ff.terminate()
            ff.wait(timeout=5)
        ev("ffmpeg_done", rc=ff.returncode, mkv_bytes=MKV.stat().st_size
           if MKV.is_file() else 0)

        # ---- 5) 停采样 / 关 exe ----
        sampler.stop()
        sampler.join(timeout=2)
        LUMA.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                                  for r in sampler.rows) + "\n", encoding="utf-8")
        ev("sampler_done", rows=len(sampler.rows))
        if exe.poll() is None:
            subprocess.run(["taskkill", "/PID", str(exe.pid), "/T", "/F"],
                           capture_output=True)
        try:
            exe.wait(timeout=6)
        except subprocess.TimeoutExpired:
            pass
        ev("exe_killed", rc=exe.poll())
    finally:
        if sampler is not None:
            sampler.stop()
            sampler.close()
        if ff is not None and ff.poll() is None:
            ff.terminate()
        # exe 完全退出后再还原，避免 Qt 析构回写覆盖
        if exe is not None and exe.poll() is None:
            subprocess.run(["taskkill", "/PID", str(exe.pid), "/T", "/F"],
                           capture_output=True)
        time.sleep(0.6)
        res = restore_settings(snap)
        ev("settings_restored", **res)
        META.write_text(json.dumps(
            {"T0_epoch": time.time() - (time.perf_counter() - T0),
             "exe_mtime": exe_mtime, "dur": DUR, "rec": [REC_W, REC_H],
             "events": EVENTS}, ensure_ascii=False, indent=2), encoding="utf-8")
        release_lock()

    print(f"\n[done] mkv={MKV}  log={FFLOG}  luma={LUMA}  meta={META}", flush=True)
    print(f"[done] QSettings 还原：{res}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
