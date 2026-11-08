# -*- coding: utf-8 -*-
r"""真机复核（只读）：① 开屏分隔线是否「提前出现」；② 落点段 t=2.40~3.20 是否还抢帧。

⛔ 不改产品源码：全部证据靠进程内 monkeypatch 采集 + ddagrab 屏幕抓帧。
⛔ 不 commit / 不打包 / 不动 发布/ 与 dist/。

两个子命令
----------
``run``      真开一次应用（``main.main()``），边抓屏边进程内取样，写 trace。
``analyze``  离屏复算 + 逐帧比对，出表（不需要锁）。

``run`` 的环境约定（与团队约定一致）
- ``QT_QPA_PLATFORM=windows``，用 ``.venv\Scripts\python.exe``
- QSettings("MimoWorks","SerialAssistant") **全量快照 → finally 原样还原**（新增键删除）
- ``_check/.app_run.lock`` 互斥（拿不到就等，≤240s）

采集内容
- ffmpeg ``ddagrab``（Desktop Duplication）120fps，crop 到「分隔线扫过的那条带」，
  ``format=gray`` 原始帧直写文件（经 pipe 会掉帧，历史实测）。
- 进程内：``paintEvent`` / ``_motion_tick`` 每次触发都记 QPC 墙钟 + 当帧几何
  （``_state`` 复算 → 分隔线 div_x/div_y/div_h 物理 px）；另挂 1ms PreciseTimer
  取样 ``_mt``（**只 append 元组**，不打 JSON，避免观察者效应）。
- 关键事件：boot / land / fade / finish / intro。

用法
----
    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_probe_divider_real.py \
        run --theme dark --tag dark_guard_on --intro-guard on
    .venv/Scripts/python.exe devtools/_probe_divider_real.py analyze --tag dark_guard_on
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "_check"
OUTDIR = CHECK / "divider_real"
LOCK = CHECK / ".app_run.lock"
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------- 工具
def _screen_phys() -> tuple[int, int]:
    """物理像素屏幕尺寸（先置 per-monitor DPI aware，与 Qt 一致）。"""
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:                       # noqa: BLE001
            ctypes.windll.user32.SetProcessDPIAware()
        u = ctypes.windll.user32
        return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    except Exception:                           # noqa: BLE001
        return 0, 0


def _lock_take(tag: str, wait_s: float = 240.0) -> bool:
    t0 = time.perf_counter()
    while True:
        try:
            LOCK.mkdir()
        except FileExistsError:
            if time.perf_counter() - t0 > wait_s:
                return False
            if int(time.perf_counter() - t0) % 10 == 0:
                print(f"[lock] 等 _check/.app_run.lock … {time.perf_counter()-t0:.0f}s",
                      flush=True)
            time.sleep(0.5)
            continue
        try:
            (LOCK / "owner.txt").write_text(
                f"pid={os.getpid()} tag={tag} at={time.strftime('%H:%M:%S')}\n",
                encoding="utf-8")
        except Exception:                       # noqa: BLE001
            pass
        return True


def _lock_drop() -> None:
    try:
        for p in LOCK.glob("*"):
            p.unlink()
        LOCK.rmdir()
    except Exception:                           # noqa: BLE001
        pass


def _qsnap() -> dict:
    from PySide6.QtCore import QSettings
    st = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: st.value(k) for k in st.allKeys()}
    st.sync()
    return snap


def _qrestore(snap: dict) -> bool:
    """还原到快照；多轮校验（防产品侧的 QSettings 析构晚于本次还原再回写）。"""
    from PySide6.QtCore import QSettings
    ok = False
    for _ in range(4):
        gc.collect()
        st = QSettings("MimoWorks", "SerialAssistant")
        for k in set(st.allKeys()) - set(snap):
            st.remove(k)
        for k, v in snap.items():
            st.setValue(k, v)
        st.sync()
        st2 = QSettings("MimoWorks", "SerialAssistant")
        now = {k: st2.value(k) for k in st2.allKeys()}
        st2.sync()
        ok = now == snap
        if ok:
            break
        time.sleep(0.15)
    return ok


# ------------------------------------------------------------- 几何（纯函数）
def _spec_geo(s, t: float, w: float, h: float):
    """复刻 MotionRenderer._state 的 (xr,yr,hr,clip,sigma,alpha)，只取几何。"""
    asp = w / max(1.0, h)

    def seg(a, b):
        return 0.0 if t <= a else 1.0 if t >= b else (t - a) / (b - a)

    def eio(x):
        x = min(1.0, max(0.0, x))
        return 4 * x ** 3 if x < 0.5 else 1.0 - (-2 * x + 2) ** 3 / 2.0

    kf = seg(s.t_blank_end, s.t_fade_end)
    h_start = s.h0 * (1.0 + 0.10 * (1.0 - kf))
    w_h = h_start * s.lockup_aspect * s.text_clip0
    y_start = 0.5 - h_start / 2.0
    x_start = 0.5 - (w_h / max(0.1, asp)) / 2.0
    if t < s.t_fade_end:
        return x_start, y_start, h_start
    if t < s.t_mark_end:
        k = eio(seg(s.t_fade_end, s.t_mark_end))
        hr = s.h0 + (s.h1 - s.h0) * k
        return x_start + (s.x1 - x_start) * k, 0.5 - hr / 2.0, hr
    return s.x1, 0.5 - s.h1 / 2.0, s.h1


def _div_rect(s, t: float, w: float, h: float) -> tuple[float, float, float]:
    """分隔线在**逻辑 px** 的 (x, y, 全高)。"""
    xr, yr, hr = _spec_geo(s, t, w, h)
    ink_h = hr * h
    bmp_w = ink_h * s.lockup_aspect / s.ink_w_frac
    bmp_h = bmp_w / s.bitmap_aspect
    left = xr * w - s.ink_left * bmp_w
    top = yr * h - s.ink_top * bmp_h
    return (left + s.divider_x * bmp_w,
            top + s.divider_y * bmp_h,
            s.divider_h * bmp_h)


def _band_for(w_log: float, h_log: float, dpr: float):
    """抓屏带（物理 px）：覆盖 t∈[0.28,1.72] 分隔线的全部位置 + 图标右缘。"""
    import splash_motion as sm
    s = sm.SPEC_PLANB
    xs, ys, hs = [], [], []
    t = 0.28
    while t <= 1.73:
        dx, dy, dh = _div_rect(s, t, w_log, h_log)
        xs.append(dx * dpr)
        ys.append(dy * dpr)
        hs.append(dh * dpr)
        t += 0.005
    pad_x, pad_y = 96, 64
    x0 = int(max(0, min(xs) - pad_x))
    x1 = int(max(xs) + pad_x)
    y0 = int(max(0, min(ys) - pad_y))
    y1 = int(max(ys[i] + hs[i] for i in range(len(ys))) + pad_y)
    if x0 % 2:
        x0 -= 1
    if y0 % 2:
        y0 -= 1
    x1 += (x1 - x0) % 2
    y1 += (y1 - y0) % 2
    return x0, y0, x1 - x0, y1 - y0, (min(xs), max(xs), min(ys), max(hs))


# --------------------------------------------------------------------- 采集
class Rec:
    """trace 写出器。

    ⛔ 热路径（每帧 paint / 每 1ms 取样）**只 append 元组**，退出时一次性落盘 ——
    第一版给 paint 打 JSONL 实测把帧率从 ~120 压到 65fps（观察者效应），已改。
    """

    def __init__(self, tag: str) -> None:
        OUTDIR.mkdir(parents=True, exist_ok=True)
        self.path = OUTDIR / f"{tag}.trace.jsonl"
        self.smp_path = OUTDIR / f"{tag}.smp.txt"
        self.paint_path = OUTDIR / f"{tag}.paint.txt"
        self.tick_path = OUTDIR / f"{tag}.tick.txt"
        self.fh = open(self.path, "w", encoding="utf-8")
        self.smp: list[tuple[float, float]] = []
        self.paints: list[tuple] = []
        self.ticks: list[tuple[float, float]] = []
        self.n = 0

    def emit(self, obj: dict) -> None:
        obj["w"] = round(time.perf_counter(), 6)
        self.fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.n += 1
        self.fh.flush()

    def flush(self) -> None:
        try:
            self.fh.flush()
            self.fh.close()
        except Exception:                       # noqa: BLE001
            pass
        try:
            with open(self.smp_path, "w", encoding="utf-8") as fh:
                for w, mt in self.smp:
                    fh.write(f"{w:.6f} {mt:.5f}\n")
        except Exception:                       # noqa: BLE001
            pass
        try:
            with open(self.paint_path, "w", encoding="utf-8") as fh:
                for row in self.paints:
                    fh.write(" ".join(f"{v:.6f}" if isinstance(v, float) else str(v)
                                      for v in row) + "\n")
        except Exception:                       # noqa: BLE001
            pass
        try:
            with open(self.tick_path, "w", encoding="utf-8") as fh:
                for w, mt in self.ticks:
                    fh.write(f"{w:.6f} {mt:.5f}\n")
        except Exception:                       # noqa: BLE001
            pass


def _install(rec: Rec, args, state: dict, band) -> None:
    import splash_anim
    import splash_motion
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication

    CLS = splash_anim._SplashWindow
    orig_paint = CLS.paintEvent
    orig_tick = CLS._motion_tick
    orig_start = CLS._start_motion
    orig_land = CLS.set_land_from_window
    orig_finish = CLS.finish
    orig_fade = CLS._fade_step
    guard = splash_motion._DIV_CUT_GUARD

    def _geo_of(self) -> dict:
        m = getattr(self, "_motion", None)
        if m is None:
            return {}
        try:
            w, h = int(self.width()), int(self.height())
            xr, yr, hr, clip, _sig, al = m._state(
                float(getattr(self, "_mt", 0.0)), w / max(1, h))
            s = m._spec
            ink_h = hr * h
            bmp_w = ink_h * s.lockup_aspect / s.ink_w_frac
            bmp_h = bmp_w / s.bitmap_aspect
            left = xr * w - s.ink_left * bmp_w
            top = yr * h - s.ink_top * bmp_h
            dpr = float(m._dpr)
            return {
                "mt": round(float(getattr(self, "_mt", 0.0)), 5),
                "div_x": round((left + s.divider_x * bmp_w) * dpr, 2),
                "div_y": round((top + s.divider_y * bmp_h) * dpr, 2),
                "div_h": round(s.divider_h * bmp_h * dpr, 2),
                "icon_cut": round((left + (s.divider_x - guard) * bmp_w) * dpr, 2),
                "clip": round(clip, 5),
                "alpha": round(al, 4),
            }
        except Exception as exc:                # noqa: BLE001
            return {"err": repr(exc)}

    def paintEvent(self, ev):                   # noqa: N802
        t0 = time.perf_counter()
        row = _geo_of(self)
        t1 = time.perf_counter()
        orig_paint(self, ev)
        t2 = time.perf_counter()
        rec.paints.append((
            round(t2, 6),
            row.get("mt", 0.0), row.get("div_x", -1.0), row.get("div_y", -1.0),
            row.get("div_h", -1.0), row.get("icon_cut", -1.0),
            row.get("clip", -1.0), row.get("alpha", -1.0),
            round((t2 - t1) * 1000.0, 3), round((t1 - t0) * 1e6, 1)))

    def _motion_tick(self):
        orig_tick(self)
        rec.ticks.append((round(time.perf_counter(), 6),
                          float(getattr(self, "_mt", 0.0))))

    def _start_motion(self):
        w_pre = time.perf_counter()          # 约等于 show()+repaint() 的时刻
        orig_start(self)
        w_post = time.perf_counter()
        m = getattr(self, "_motion", None)
        app = QApplication.instance()
        scr = []
        try:
            for sc in QApplication.screens():
                scr.append([sc.name(), list(sc.geometry().getRect()),
                            float(sc.devicePixelRatio())])
        except Exception:                       # noqa: BLE001
            pass
        rec.emit({
            "k": "boot",
            "wlog": int(self.width()), "hlog": int(self.height()),
            "dpr_win": float(self.devicePixelRatioF()),
            "dpr_renderer": float(getattr(m, "_dpr", 0)) if m else 0.0,
            "screens": scr,
            "hz": int(getattr(self, "_hz", 0)),
            "interval_ms": int(self._motion_timer.interval()),
            "scheme": getattr(m, "_scheme", "") if m else "",
            "clear_w": int(getattr(m, "_clear_w", 0)) if m else 0,
            "blur_w": int(getattr(m, "_blur_w", 0)) if m else 0,
            "band": list(band), "tol": args.tol,
            "spec": {"t_fade_end": splash_motion.SPEC_PLANB.t_fade_end,
                     "t_mark_end": splash_motion.SPEC_PLANB.t_mark_end,
                     "t_line_ms": splash_motion.SPEC_PLANB.t_line_ms,
                     "t_text_end": splash_motion.SPEC_PLANB.t_text_end,
                     "t_hold_end": splash_motion.SPEC_PLANB.t_hold_end,
                     "t_land_end": splash_motion.SPEC_PLANB.t_land_end,
                     "div_cut_guard": guard,
                     "divider_x": splash_motion.SPEC_PLANB.divider_x},
            "intro_guard_bypass": bool(state.get("bypass")),
            "intro_patched": bool(state.get("patched")),
            # 时间锚：_start_motion 入口（≈ show()+repaint()）与 QElapsedTimer 零点
            "w_show": round(w_pre, 6),
            "w_post": round(w_post, 6),
            "t0": round(w_post - float(getattr(self, "_mt", 0.0)), 6),
            "ff_start": round(float(state.get("ff_start", 0.0)), 6),
        })
        # 1ms 取样（只 append 元组；--no-sampler 时完全关掉，用于量化观察者效应）
        if args.sampler:
            smp = QTimer(self)
            smp.setTimerType(Qt.TimerType.PreciseTimer)
            smp.setInterval(1)

            def _s():
                rec.smp.append((time.perf_counter(),
                                float(getattr(self, "_mt", 0.0))))
            smp.timeout.connect(_s)
            smp.start()
            self._probe_smp = smp
        if app is not None:
            QTimer.singleShot(int(args.quit_after * 1000.0), app.quit)

    def set_land_from_window(self, win):
        orig_land(self, win)
        m = getattr(self, "_motion", None)
        rec.emit({"k": "land", "mt": round(float(self._mt), 5),
                  "land": [round(v, 5) for v in getattr(m, "_land", (0, 0, 0))]})

    def finish(self):
        if not getattr(self, "_done", False):
            rec.emit({"k": "finish", "mt": round(float(self._mt), 5)})
        orig_finish(self)

    def _fade_step(self):
        orig_fade(self)
        m = getattr(self, "_motion", None)
        rec.emit({"k": "fade", "mt": round(float(self._mt), 5),
                  "fk": round(float(getattr(m, "_fade_k", 0.0)), 4) if m else 0.0})

    CLS.paintEvent = paintEvent
    CLS._motion_tick = _motion_tick
    CLS._start_motion = _start_motion
    CLS.set_land_from_window = set_land_from_window
    CLS.finish = finish
    CLS._fade_step = _fade_step

    if args.intro_guard == "off":
        # 只在**本进程**里旁路 `_play_intro` 的 `_skip_startup_anim` 守卫，
        # 复刻「修复前」的 4 路 QGraphicsOpacityEffect 入场淡入。
        import builtins
        orig_import = builtins.__import__

        def _patch_mw(mw) -> None:
            def patched(self):
                saved = getattr(self, "_skip_startup_anim", False)
                self._skip_startup_anim = False
                try:
                    orig_intro(self)
                finally:
                    self._skip_startup_anim = saved
            orig_intro = mw.MainWindow._play_intro
            mw.MainWindow._play_intro = patched
            state["patched"] = True
            state["bypass"] = True
            rec.emit({"k": "intro_patch", "at": round(time.perf_counter(), 6)})

        def _imp(name, *a, **kw):
            mod = orig_import(name, *a, **kw)
            if name == "main_window" and not state.get("patched"):
                try:
                    _patch_mw(sys.modules["main_window"])
                except Exception as exc:        # noqa: BLE001
                    rec.emit({"k": "intro_patch_fail", "err": repr(exc)})
            return mod

        builtins.__import__ = _imp


def cmd_run(args) -> int:
    if not _lock_take(args.tag):
        print("[fatal] 240s 内没拿到 _check/.app_run.lock（有别的真机运行占着）",
              flush=True)
        return 2
    OUTDIR.mkdir(parents=True, exist_ok=True)
    rec = Rec(args.tag)
    snap = _qsnap()
    state: dict = {}
    ff = None
    rc = 1
    t_ff = 0.0
    try:
        # 主题：直接改 theme_mode（finally 原样还原）
        from PySide6.QtCore import QSettings
        st = QSettings("MimoWorks", "SerialAssistant")
        st.setValue("theme_mode", args.theme)
        st.sync()

        sw, sh = _screen_phys()
        if not sw or not sh:
            sw, sh = 3200, 2000
        wlog, hlog, dpr = args.wlog, args.hlog, args.dpr
        band = _band_for(wlog, hlog, dpr)
        meta = {"screen_phys": [sw, sh], "band": list(band[:4]),
                "band_span": list(band[4]), "theme": args.theme,
                "tag": args.tag, "intro_guard": args.intro_guard,
                "secs": args.secs, "fps": args.fps}
        print(f"[run] screen={sw}x{sh} band={band[:4]} "
              f"span(x {band[4][0]:.0f}..{band[4][1]:.0f} y {band[4][2]:.0f}"
              f"..{band[4][2]+band[4][3]:.0f})", flush=True)
        (OUTDIR / f"{args.tag}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

        x, y, w, h = band[0], band[1], band[2], band[3]
        raw = OUTDIR / f"{args.tag}.raw"
        # ⛔ 必须让 ddagrab **只抓 band 这一块**（video_size/offset_*）：
        #    整屏 3200x2000 全帧 hwdownload 是 3GB/s 级，实测把开屏帧率从
        #    136fps 拖到 76fps（观察者效应）。裁到带内后下载量降到 ~0.33GB/s。
        vf = (f"ddagrab=0:framerate={args.fps}:video_size={w}x{h}"
              f":offset_x={x}:offset_y={y}:draw_mouse=0,"
              f"hwdownload,format=bgra,format=gray")
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "lavfi", "-i", vf, "-t", f"{args.secs}",
               "-c:v", "rawvideo", "-f", "rawvideo", "-y", str(raw)]
        t_ff = time.perf_counter()
        state["ff_start"] = t_ff
        if args.record:
            ff = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE)

        _install(rec, args, state, band[:4])

        import main as app_main
        argv_saved = sys.argv[:]
        sys.argv = ["main.py"]
        try:
            rc = int(app_main.main() or 0)
        finally:
            sys.argv = argv_saved
        print(f"[run] main() 返回 rc={rc}", flush=True)
    except BaseException as exc:                # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[run] 异常：{exc!r}", flush=True)
    finally:
        try:
            if ff is not None:
                try:
                    ff.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    ff.kill()
                err = b""
                try:
                    err = ff.stderr.read() or b""
                finally:
                    ff.stderr.close()
                if err:
                    print(f"[run] ffmpeg: {err.decode('utf-8', 'replace')[:300]}",
                          flush=True)
        except Exception:                       # noqa: BLE001
            pass
        rec.emit({"k": "exit", "ff_start": round(t_ff, 6)})
        rec.flush()
        gc.collect()
        time.sleep(0.05)
        ok = _qrestore(snap)
        print(f"[run] QSettings 还原={'OK' if ok else 'FAILED'}", flush=True)
        if state.get("bypass") and not state.get("patched"):
            print("[run] ⚠ intro 守卫旁路未生效（未打到 main_window）", flush=True)
        _lock_drop()
        print(f"[run] trace={rec.path} smp={rec.smp_path} n={rec.n}", flush=True)
    return 0 if rc == 0 else 1


# --------------------------------------------------------------------- 分析
def _load_trace(tag: str):
    path = OUTDIR / f"{tag}.trace.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    boot = next((r for r in rows if r.get("k") == "boot"), None)
    land = next((r for r in rows if r.get("k") == "land"), None)
    return rows, boot, land


PAINT_COLS = ("w", "mt", "div_x", "div_y", "div_h", "icon_cut", "clip",
              "alpha", "paint_ms", "geo_us")


def _load_paints(tag: str) -> list[dict]:
    path = OUTDIR / f"{tag}.paint.txt"
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        p = line.split()
        if len(p) != len(PAINT_COLS):
            continue
        out.append({k: float(v) for k, v in zip(PAINT_COLS, p)})
    return out


def _load_pairs(path: Path) -> list[tuple[float, float]]:
    out = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        p = line.split()
        if len(p) == 2:
            out.append((float(p[0]), float(p[1])))
    return out


def _offscreen_table(wlog: int, hlog: int, dpr: float, band, land, scheme: str,
                     tol: int, t0: float = 0.26, t1: float = 2.02,
                     step: float = 0.0025):
    """离屏按真机 w/h/dpr 复算：逐 t 给出分隔线几何 + 带内每列最大竖游程。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import numpy as np
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    import splash_motion as sm

    r = sm.MotionRenderer(scheme, dpr, wlog, hlog)
    r.set_land(*land)
    s = r._spec
    W, H = int(round(wlog * dpr)), int(round(hlog * dpr))
    x0, y0, bw, bh = band
    img = QImage(W, H, QImage.Format.Format_RGBA8888)
    bg = QColor("#ffffff" if scheme == "light" else "#0d0d0f")

    def max_vrun(mask):
        hh, ww = mask.shape
        run = np.zeros(ww, dtype=np.int16)
        best = np.zeros(ww, dtype=np.int16)
        for yy in range(hh):
            run += 1
            run *= mask[yy]
            np.maximum(best, run, out=best)
        return best

    rows = []
    t = t0
    while t <= t1:
        img.fill(bg)
        p = QPainter(img)
        p.scale(dpr, dpr)
        r.paint(p, wlog, hlog, t)
        p.end()
        arr = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(
            H, img.bytesPerLine() // 4, 4)
        sub = arr[y0:y0 + bh, x0:x0 + bw, :3].astype(np.int16)
        bgc = np.array([bg.red(), bg.green(), bg.blue()], dtype=np.int16)
        mask = (np.abs(sub - bgc).max(axis=2) > tol)
        run = max_vrun(mask)
        dx, dy, dh = _div_rect(s, t, wlog, hlog)
        dxp, dyp, dhp = dx * dpr, dy * dpr, dh * dpr
        # 带内坐标
        lo = int(round(dxp - x0))
        tall = np.where(run >= 24)[0]
        icon_r = int(tall[-1]) + x0 if tall.size else None
        w_lo = max(0, lo - 16)
        w_hi = min(bw, lo + 17)
        rows.append({
            "t": round(t, 4), "div_x": round(dxp, 2), "div_y": round(dyp, 2),
            "div_h": round(dhp, 2), "icon_r": icon_r,
            "div_run": int(run[w_lo:w_hi].max()) if w_hi > w_lo else -1,
            "div_run_x": int(w_lo + int(np.argmax(run[w_lo:w_hi]))) + x0 if w_hi > w_lo else -1,
            "run_max": int(run.max()),
            # 竖游程剖面（降采样 128 格，取格内最大）—— 作为「这一帧是 t 几」的
            # 内容指纹，用于视频帧↔离屏 t 的全局匹配（比单一 icon_r 稳）。
            "prof": _down_profile(run, 128),
        })
        t += step
    return rows, (W, H)


_PROF_BINS = 128


def _down_profile(run, bins: int = _PROF_BINS):
    """把整带的「每列最大竖游程」压成 bins 格（格内取最大），作内容指纹。"""
    import numpy as np
    n = run.shape[0]
    edges = np.linspace(0, n, bins + 1).astype(int)
    out = np.zeros(bins, dtype=np.float32)
    for i in range(bins):
        a, b = edges[i], max(edges[i] + 1, edges[i + 1])
        out[i] = float(run[a:b].max())
    return out


def _video_frames(raw_path: Path, bw: int, bh: int):
    import numpy as np
    fb = bw * bh
    size = raw_path.stat().st_size
    n = size // fb
    mm = np.memmap(raw_path, dtype=np.uint8, mode="r", shape=(n, bh, bw))
    return mm, n


def _max_vrun(mask):
    import numpy as np
    hh, ww = mask.shape
    run = np.zeros(ww, dtype=np.int16)
    best = np.zeros(ww, dtype=np.int16)
    for yy in range(hh):
        run += 1
        run *= mask[yy]
        np.maximum(best, run, out=best)
    return best


def cmd_analyze(args) -> int:
    import numpy as np
    rows, boot, land = _load_trace(args.tag)
    meta = json.loads((OUTDIR / f"{args.tag}.meta.json").read_text(encoding="utf-8"))
    band = tuple(boot["band"])
    tol = int(boot.get("tol", args.tol))
    wlog, hlog = int(boot["wlog"]), int(boot["hlog"])
    dpr = float(boot["dpr_renderer"] or boot["dpr_win"])
    scheme = boot["scheme"] or meta["theme"]
    spec = boot["spec"]
    t_mark = float(spec["t_mark_end"])
    t_line = float(spec["t_line_ms"])
    print(f"[an] {args.tag}: wlog={wlog} hlog={hlog} dpr={dpr} scheme={scheme} "
          f"band={band} tol={tol} hz={boot.get('hz')} "
          f"interval={boot.get('interval_ms')}ms "
          f"intro_guard_bypass={boot.get('intro_guard_bypass')}")

    # ---- 视频帧
    raw = OUTDIR / f"{args.tag}.raw"
    mm, n = _video_frames(raw, band[2], band[3])
    print(f"[an] 视频帧 {n} @ {meta['fps']}fps = {n/meta['fps']:.2f}s")

    # ---- 离屏表
    lt = land["land"] if land else [0.03, 0.0785, 0.085]
    table, _ = _offscreen_table(wlog, hlog, dpr, band, lt, scheme, tol)
    tt = np.array([r["t"] for r in table])
    t_divx = np.array([r["div_x"] for r in table])
    t_divy = np.array([r["div_y"] for r in table])
    t_divh = np.array([r["div_h"] for r in table])
    t_iconr = np.array([r["icon_r"] if r["icon_r"] is not None else -1
                        for r in table], dtype=float)

    # ---- 逐帧测量（先全部算一遍 mode / 游程 / 图标右缘，再定「哪几帧是开屏」）
    x0, y0, bw, bh = band
    frames = []
    prev = None
    for i in range(n):
        f = np.asarray(mm[i])
        hist = np.bincount(f.ravel(), minlength=256)
        bg = int(hist.argmax())
        frac_mode = float(hist[bg] / f.size)
        diff = np.abs(f.astype(np.int16) - bg)
        mask = diff > tol
        run = _max_vrun(mask)
        # 图标右缘：门槛取 24 phys px（≈12 逻辑）—— 分隔线的 AA 残影只有 ≤2px，
        # 而图标自身的「车顶细尾/字母横笔」会有几十 px，门槛太高会把它们算成
        # 「空隙里的竖线」（第一版用 100 就误判过）。
        tall = np.where(run >= 24)[0]
        frames.append({"i": i, "bg": bg, "frac_mode": frac_mode,
                       "icon_r": int(tall[-1]) + x0 if tall.size else None,
                       "run_max": int(run.max()),
                       "dup": bool(prev is not None and np.array_equal(f, prev)),
                       "_run": run})
        prev = f

    # ---- 时间对齐（两段式）
    # ① 锚点：开屏的「空白段」是整幅纯背景（frac_mode≈1），桌面/主窗不会这么纯。
    #    取图标出现前最后一个纯背景帧 → 它对应 mt≈t_blank_end(0.30)。
    fps = float(meta["fps"])
    t_blank = float(spec.get("t_fade_end", 1.0))
    blank = [r for r in frames if r["frac_mode"] >= 0.97 and r["i"] < n * 0.75]
    if not blank:
        print("[an] ✗ 没找到开屏空白段（纯背景帧），无法对齐")
        return 1
    # 最长连续段
    best_run = []
    cur = [blank[0]]
    for r in blank[1:]:
        if r["i"] == cur[-1]["i"] + 1:
            cur.append(r)
        else:
            if len(cur) > len(best_run):
                best_run = cur
            cur = [r]
    if len(cur) > len(best_run):
        best_run = cur
    i_blank0 = best_run[0]["i"]
    i_blank1 = best_run[-1]["i"]
    t_blank_end = 0.30
    a0 = t_blank_end + 0.02 - i_blank1 / fps
    print(f"[an] 空白段帧 {i_blank0}..{i_blank1} "
          f"({(i_blank1-i_blank0+1)/fps:.2f}s) → 锚点 i0={i_blank1} "
          f"≈ mt={t_blank_end:.2f}s，a0={a0:+.4f}")

    def pred_icon(mt):
        if mt < tt[0] or mt > tt[-1]:
            return None
        return float(np.interp(mt, tt, t_iconr))

    # ② 全局匹配：用「竖游程剖面指纹」把每个采样帧对到离屏表里的 t。
    #    ⛔ 不要用「按当前 a 选窗口再算中位误差」的迭代拟合：窗口会随 a 一起
    #       移动，代价函数非单峰，第一版就落到过 340px 的假最优。
    from numpy import stack
    tprof = stack([r["prof"] for r in table])
    samples = []
    for rec in frames:
        if rec["i"] < i_blank0 or rec["i"] % 10:
            continue
        pf = _down_profile(rec["_run"])
        if float(pf.max()) < 20.0:      # 纯空白帧的剖面全是 0，对到哪个 t 都对
            continue
        d = np.abs(tprof - pf).mean(axis=1)
        j = int(d.argmin())
        samples.append((rec["i"], float(tt[j]), float(d[j])))
    if len(samples) < 10:
        print("[an] ✗ 匹配样本不足，无法对齐")
        return 1
    ds = sorted(s[2] for s in samples)
    thr = max(1.2, ds[len(ds) // 2] * 2.5)
    good = [s for s in samples if s[2] <= thr]
    # a_i 用「最大簇」而不是总中位：个别帧指纹退化（图标刚出现/已停住）会给出
    # 完全不同的 t，直接取中位会被拉偏。
    cand = sorted(((t - i / fps), i, t, d) for i, t, d in good)
    best_cluster: list[tuple] = []
    for k in range(len(cand)):
        cl = [c for c in cand if abs(c[0] - cand[k][0]) <= 0.030]
        if len(cl) > len(best_cluster):
            best_cluster = cl
    a_list = sorted(c[0] for c in best_cluster)
    A = a_list[len(a_list) // 2]
    dev = sorted(abs(v - A) * 1000 for v in a_list)
    print(f"[an] 剖面匹配：样本 {len(samples)} 帧，容差内 {len(good)}，"
          f"最大簇 {len(best_cluster)} 帧；匹配残差 中位 {ds[len(ds)//2]:.2f} / "
          f"P95 {ds[int(len(ds)*.95)]:.2f} phys px；"
          f"a 离散 P95 {dev[int(len(dev)*0.95)]:.1f}ms max {dev[-1]:.1f}ms")
    print(f"[an] 时间对齐：mt = {A:+.4f} + i/{fps:g}"
          f"（空白段锚点估计 a0={a0:+.4f}，差 {(A-a0)*1000:+.0f}ms）")

    # ③ 绝对时间锚：_start_motion 入口 ≈ show()+repaint()（开屏首帧上屏），
    #    QElapsedTimer 零点 t0 由 boot 记录直接给出。两者都来自 QPC，
    #    **与画面内容无关** —— 用来独立校验上面的内容匹配、并暴露
    #    ddagrab 是否发生了时间轴压缩。
    A_wall = None
    if boot.get("w_show") and boot.get("t0") and boot.get("ff_start"):
        i_fu = best_run[0]["i"]
        A_wall = (float(boot["w_show"]) - i_fu / fps) - float(boot["t0"])
        print(f"[an] 墙钟锚（show 时刻 + QElapsedTimer 零点）："
              f"mt = {A_wall:+.4f} + i/{fps:g}（首帧纯背景 i={i_fu}，"
              f"与内容匹配差 {(A_wall-A)*1000:+.0f}ms）")

    # 全量残差（图标可测的帧）
    res = []
    for rec in frames:
        if rec["icon_r"] is None or rec["i"] < i_blank0:
            continue
        mt = A + rec["i"] / fps
        p = pred_icon(mt)
        if p and p > 0 and mt < 1.66:
            res.append(abs(rec["icon_r"] - p))
    if res:
        res.sort()
        print(f"[an] 全量残差（mt<1.66）：n={len(res)} 中位 {res[len(res)//2]:.1f} "
              f"P95 {res[int(len(res)*0.95)]:.1f} max {res[-1]:.1f} phys px")

    # ---- 逐帧分隔线窗口统计（只取 mt∈[0,2.10] 的开屏帧）
    out = []
    for rec in frames:
        if rec["i"] < i_blank0:          # 开屏窗口出现之前的桌面帧
            continue
        mt = A + rec["i"] / fps
        if not (-0.02 <= mt <= 2.10):
            continue
        divx = float(np.interp(mt, tt, t_divx))
        divy = float(np.interp(mt, tt, t_divy))
        divh = float(np.interp(mt, tt, t_divh))
        run = rec["_run"]
        lo = int(round(divx - x0))
        w_lo, w_hi = max(0, lo - 16), min(bw, lo + 17)
        seg = run[w_lo:w_hi]
        k = int(np.argmax(seg)) if seg.size else 0
        # 空隙带：图标右缘 ~ 分隔线左缘
        ir = rec["icon_r"]
        g_lo = (ir - x0 + 12) if ir else max(0, lo - 300)
        g_hi = max(g_lo, lo - 8)
        gap_run = int(run[g_lo:g_hi].max()) if g_hi > g_lo else 0
        out.append({"i": rec["i"], "mt": round(mt, 4),
                    "div_x": round(divx, 1), "div_y": round(divy, 1),
                    "div_h": round(divh, 1),
                    "run": int(seg.max()) if seg.size else -1,
                    "run_x": int(w_lo + k) + x0,
                    "gap_run": gap_run,
                    "icon_r": ir, "dup": rec["dup"],
                    "band_max": rec["run_max"],
                    "pred_run": int(np.interp(mt, tt,
                                              [r["div_run"] for r in table]))})

    pre = [r for r in out if r["mt"] < t_mark - 0.004]
    post = [r for r in out if r["mt"] >= t_mark]
    if not out:
        print("[an] ✗ 没有落在 mt∈[0,2.10] 的开屏帧（对齐失败）")
        return 1
    print(f"\n[an] 开屏可见帧 {len(out)}（mt {out[0]['mt']:.2f}~{out[-1]['mt']:.2f}）"
          f"  t<{t_mark}: {len(pre)} 帧   t>={t_mark}: {len(post)} 帧")

    def first_at(key, thr):
        for r in out:
            if r[key] >= thr:
                return r
        return None

    print(f"[an] t<{t_mark} 窗口：分隔线列最大游程 = "
          f"{max([r['run'] for r in pre], default=0)} phys px"
          f"（{max(pre, key=lambda r: r['run'])['mt']:.2f}s）")
    print(f"[an] t<{t_mark} 窗口：空隙带（图标右缘~分隔线）最大游程 = "
          f"{max([r['gap_run'] for r in pre], default=0)} phys px")
    for thr in (3, 5, 8, 16, 30, 60):
        r = first_at("run", thr)
        if r:
            kind = "早于竖线时刻" if r["mt"] < t_mark - 0.004 else "竖线展开期"
            print(f"[an] 首次 run>={thr:3d} phys px：t={r['mt']:.3f}s "
                  f"(frame {r['i']}, {kind}) 高={r['run']} 列x={r['run_x']} "
                  f"div_x={r['div_x']:.0f} icon_r={r['icon_r']}")
        else:
            print(f"[an] 首次 run>={thr:3d}：无")
    # 分隔线全高一半
    r = first_at("run", 0)  # noqa
    half = None
    for r in out:
        if r["div_h"] and r["run"] >= 0.5 * r["div_h"]:
            half = r
            break
    if half:
        print(f"[an] 首次 run>=0.5*div_h：t={half['mt']:.3f}s 高={half['run']}"
              f"/{half['div_h']:.0f} div_x={half['div_x']:.0f} x={half['run_x']}")

    # 命中列是否跟着图标走（x 随 div_x 移动）
    hits = [r for r in out if r["run"] >= 60]
    if hits:
        d = sorted(r["run_x"] - r["div_x"] for r in hits)
        print(f"[an] 展开期命中列 x − 预测 div_x：n={len(d)} 中位 {d[len(d)//2]:.1f}"
              f" 范围 [{d[0]:.1f}, {d[-1]:.1f}] phys px"
              f"（div_x 在 {hits[0]['div_x']:.0f}→{hits[-1]['div_x']:.0f} 之间移动）")
    early_hits = [r for r in pre if r["run"] >= 8]
    if early_hits:
        d = [r["run_x"] - r["div_x"] for r in early_hits]
        print(f"[an] ⚠ 展开前已出现 run>=8 的帧 {len(early_hits)} 个，"
              f"命中列 x−div_x = {[round(v,1) for v in d[:10]]}")
    print(f"[an] t<{t_mark} 窗口 band_max 最大 = "
          f"{max([r['band_max'] for r in pre], default=0)} phys px"
          f"（含图标自身竖笔画，仅供对照）")

    # ---- 表格
    print(f"\n[an] {'t':>5} {'frame':>6} {'div_x':>7} {'run':>5} {'@x':>6} "
          f"{'gap':>4} {'pred':>5} {'icon_r':>6} {'dup':>4}")
    last = -1.0
    for r in out:
        if r["mt"] - last >= 0.05 or abs(r["mt"] - t_mark) < 0.01:
            last = r["mt"]
            print(f"     {r['mt']:5.2f} {r['i']:6d} {r['div_x']:7.1f} "
                  f"{r['run']:5d} {r['run_x']:6d} {r['gap_run']:4d} "
                  f"{r['pred_run']:5d} {str(r['icon_r']):>6} "
                  f"{'Y' if r['dup'] else '':>4}")

    # ---- 落点段帧间隔（进程内 paint 时间戳）
    paints = _load_paints(args.tag)
    print(f"[an] paint 记录 {len(paints)} 条；"
          f"1ms 取样 {len(_load_pairs(OUTDIR / f'{args.tag}.smp.txt'))} 条")
    return 0


def cmd_landing(args) -> int:
    """落点段帧间隔分析（只读 paint/tick/smp，不需要视频）。"""
    rows, boot, _land = _load_trace(args.tag)
    t_hold = float(boot["spec"]["t_hold_end"])
    t_land = float(boot["spec"]["t_land_end"])
    paints = _load_paints(args.tag)
    print(f"\n[land] {args.tag}: paint {len(paints)} 条  hz={boot.get('hz')} "
          f"interval={boot.get('interval_ms')}ms  "
          f"intro_guard_bypass={boot.get('intro_guard_bypass')}")

    def stats(name, seq):
        if len(seq) < 3:
            print(f"  {name}: 帧数不足 ({len(seq)})")
            return
        iv = [round((seq[i] - seq[i - 1]) * 1000.0, 2) for i in range(1, len(seq))]
        iv_s = sorted(iv)
        n = len(iv_s)
        p50 = iv_s[n // 2]
        p95 = iv_s[min(n - 1, int(n * 0.95))]
        big = [v for v in iv if v > 6.6]
        huge = [v for v in iv if v > 20]
        dur = (seq[-1] - seq[0]) * 1000.0
        print(f"  {name}: 帧数={len(seq)} 时长={dur:.0f}ms 等效帧率="
              f"{(len(seq)-1)/max(0.001, seq[-1]-seq[0]):.1f}fps "
              f"P50={p50:.2f} P95={p95:.2f} max={max(iv):.2f}ms "
              f">6.6ms={len(big)} >20ms={len(huge)}")
        if big:
            print(f"     >6.6ms 明细(ms): {big[:20]}")

    seg = [r for r in paints if t_hold - 0.02 <= r["mt"] <= t_land + 0.02]
    stats(f"落点段 {t_hold:.2f}~{t_land:.2f}s", [r["w"] for r in seg])
    seg2 = [r for r in paints if 1.90 <= r["mt"] <= 2.40]
    stats("对照 1.90~2.40s（停留）", [r["w"] for r in seg2])
    seg3 = [r for r in paints if 0.60 <= r["mt"] <= 1.60]
    stats("对照 0.60~1.60s（淡入+左移）", [r["w"] for r in seg3])
    seg4 = [r for r in paints if 2.40 <= r["mt"] <= 3.50]
    stats("全程 2.40~3.50s（含淡出）", [r["w"] for r in seg4])

    # 重复帧（几何完全一致）
    dup = 0
    for i in range(1, len(seg)):
        a, b = seg[i - 1], seg[i]
        if (a["div_x"] == b["div_x"] and a["div_h"] == b["div_h"]
                and a["alpha"] == b["alpha"]):
            dup += 1
    print(f"  落点段重复几何帧 = {dup}/{max(1,len(seg)-1)}")
    if seg:
        pm = sorted(r["paint_ms"] for r in seg)
        print(f"  落点段单帧 paint 耗时：n={len(pm)} P50={pm[len(pm)//2]:.2f} "
              f"P95={pm[int(len(pm)*0.95)]:.2f} max={pm[-1]:.2f}ms")
        gu = sorted(r["geo_us"] for r in seg)
        print(f"  （探针自身每帧开销 中位 {gu[len(gu)//2]:.1f}µs "
              f"max {gu[-1]:.1f}µs）")

    # 1ms 取样：GUI 线程停顿
    smp_path = OUTDIR / f"{args.tag}.smp.txt"
    ws = []
    for line in smp_path.read_text(encoding="utf-8").splitlines():
        p = line.split()
        if len(p) == 2:
            ws.append((float(p[0]), float(p[1])))
    if len(ws) > 10:
        t0 = ws[0][0]
        gaps = [(ws[i][0] - ws[i - 1][0]) * 1000.0 for i in range(1, len(ws))]
        seg_gap = [(ws[i][0] - t0, ws[i][1], gaps[i - 1]) for i in range(1, len(ws))
                   if t_hold - 0.02 <= ws[i][1] <= t_land + 0.02]
        gs = sorted(g for _, _, g in seg_gap)
        if gs:
            print(f"  1ms 取样 @落点段：n={len(gs)} 间隔 P50={gs[len(gs)//2]:.2f} "
                  f"P95={gs[int(len(gs)*0.95)]:.2f} max={gs[-1]:.2f}ms")
        top = sorted([(g, mt) for _, mt, g in seg_gap], reverse=True)[:6]
        print(f"  落点段最大停顿（ms@mt）: "
              f"{[(round(g,1), round(mt,3)) for g, mt in top]}")
        # 全段停顿（排除落点）
        big = sorted([(g, ws[i][1]) for i, g in enumerate(gaps, start=1) if g > 20],
                     reverse=True)[:8]
        print(f"  全程 >20ms 停顿（ms@mt）: "
              f"{[(round(g,1), round(mt,2)) for g, mt in big]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--theme", default="dark", choices=["dark", "light"])
    r.add_argument("--tag", required=True)
    r.add_argument("--intro-guard", default="on", choices=["on", "off"])
    r.add_argument("--secs", type=float, default=4.8)
    r.add_argument("--fps", type=int, default=120)
    r.add_argument("--quit-after", type=float, default=4.2)
    r.add_argument("--tol", type=int, default=2)
    r.add_argument("--sampler", type=int, default=1, choices=[0, 1])
    r.add_argument("--record", type=int, default=1, choices=[0, 1])
    r.add_argument("--wlog", type=float, default=1600.0)
    r.add_argument("--hlog", type=float, default=1000.0)
    r.add_argument("--dpr", type=float, default=2.0)
    r.set_defaults(func=cmd_run)

    a = sub.add_parser("analyze")
    a.add_argument("--tag", required=True)
    a.add_argument("--tol", type=int, default=2)
    a.set_defaults(func=cmd_analyze)

    l = sub.add_parser("landing")
    l.add_argument("--tag", required=True)
    l.set_defaults(func=cmd_landing)

    args = ap.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
