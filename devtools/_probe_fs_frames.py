# -*- coding: utf-8 -*-
r"""最大化/还原 逐帧诊断探针（2026-11-07，只读：不改产品源码）。

目标（对应 team-lead 诊断任务）：
  1. 真机录 3 轮「点最大化 → 停 1s → 点还原 → 停 1s」，ddagrab 60fps。
  2. 输出逐帧表：全屏平均亮度差 Δluma、像素差面积占比；Δluma>1.5 或
     「出现不该出现的旧内容/桌面/白底」的帧单独点名，并标出属于哪一段。
  3. 判断卡顿性质：掉帧 / 运动不连续 / 时序错配 —— 全部用数字说话。
  4. 给出最小改动方案（函数名 + 行号）。

做法：
  - 黑底全屏垫底窗 + 主窗置顶（防 WorkBuddy 抢镜）。桌面泄漏在垫底下呈
    「窗口区域变纯黑」，用 bbox 收缩检测。
  - 父进程：互斥锁 _check/.app_run.lock（最多等 240s）→ 快照 QSettings →
    起 ddagrab → 子进程跑 app → 收尾 → 还原 QSettings → 逐帧分析。
  - 子进程：包 _on_cover_tick / _begin_window_cover / _finish_window_cover
    记录每帧耗时与几何；20ms 采样全量状态；os._exit 硬退。
  - 时间轴对齐：子进程记 time.time()，父进程记 ffmpeg 启动墙钟；再用
    「首个普通宽帧」与「window shown」事件二次校准（录屏启动抖动 1~4s）。

用法：.venv/Scripts/python.exe devtools/_probe_fs_frames.py
      .venv/Scripts/python.exe devtools/_probe_fs_frames.py --analyze-only
"""
import json
import glob
import os
import shutil
import subprocess
import sys
import threading
import time

_T0 = time.perf_counter()


def _ms() -> float:
    return (time.perf_counter() - _T0) * 1000.0


_ROUNDS = 3
_ROUND_GAP_MS = 2000          # 每轮：max → 1000ms → restore → 1000ms
_FIRST_CLICK_MS = 1000

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

OUT = os.path.join(_ROOT, "_check", "winfx_fs_frames")
os.makedirs(OUT, exist_ok=True)
LOCK = os.path.join(_ROOT, "_check", ".app_run.lock")
# ⛔ 默认不落盘快照：QPixmap.save 一张 3200x2000 PNG 要 60~150ms，会把
#    「点击同步成本 / 点击→首帧」测成假的。需要快照证据时加 --dump。
_DUMP = "--dump" in sys.argv

_SNAPSHOT: dict = {}
_SAMPLES: list[dict] = []
_EVENTS: list[tuple[float, str]] = []
_TICKS: list[dict] = []
_COVERS: list[dict] = []
_PAINTS: list[float] = []


def _ev(tag: str) -> None:
    _EVENTS.append((time.time(), tag))
    print(f"EVENT {_ms():7.1f} {tag}", flush=True)


# --------------------------------------------------------------------- 子进程
def run_app() -> int:
    import faulthandler

    faulthandler.dump_traceback_later(45, exit=True)
    from PySide6.QtCore import QSettings
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication(sys.argv)
    import ui_anim
    from main_window import MainWindow

    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    scr = app.primaryScreen()
    sg = scr.geometry()

    backdrop = QWidget(None, Qt.Window | Qt.FramelessWindowHint
                       | Qt.WindowStaysOnTopHint)
    backdrop.setAttribute(Qt.WA_ShowWithoutActivating)
    backdrop.setAutoFillBackground(True)
    pal = backdrop.palette()
    pal.setColor(backdrop.backgroundRole(), QColor(0, 0, 0))
    backdrop.setPalette(pal)
    backdrop.setWindowFlag(Qt.WindowTransparentForInput, True)
    backdrop.setGeometry(sg)
    backdrop.show()
    backdrop.raise_()

    window = MainWindow()
    try:
        window._port_watch.stop()
    except Exception:                 # noqa: BLE001
        pass
    w, h = 1134, 820
    window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    window.setGeometry(sg.x() + (sg.width() - w) // 2,
                       sg.y() + max(60, (sg.height() - h) // 3), w, h)
    window.show()
    window.raise_()
    window.activateWindow()
    try:
        window._start_snap_worker()
    except Exception:                 # noqa: BLE001
        pass
    _ev("window_shown")
    print("STAGE: window shown", flush=True)

    # ---------------- 包装覆盖层三件套（记录每 tick 成本与几何） ----------------
    _orig_tick = MainWindow._on_cover_tick
    _orig_begin = MainWindow._begin_window_cover
    _orig_finish = MainWindow._finish_window_cover
    _orig_build = MainWindow._build_fs_idle_pix
    _orig_prescale = MainWindow._cover_prescale
    _dump_n = {"fs": 0, "cov": 0}

    def _wrap_prescale(self, pix, start, end):
        t0 = time.perf_counter()
        out = _orig_prescale(self, pix, start, end)
        dt = (time.perf_counter() - t0) * 1000.0
        if pix is not None and not pix.isNull():
            _ev(f"prescale {pix.width()}x{pix.height()}→"
                f"{out.width() if out is not None else 0}x"
                f"{out.height() if out is not None else 0} {dt:.1f}ms")
        return out

    from PySide6.QtWidgets import QLabel as _QLabel
    _orig_qlabel_show = _QLabel.show

    # 覆盖层 paintEvent 计时（区分「客户端绘制」和「DWM 合成」谁在吃时间）
    import main_window as _mw
    _cov_cls = getattr(_mw, "_CoverLabel", None)
    if _cov_cls is not None:
        _orig_paint = _cov_cls.paintEvent

        def _wrap_paint(self, ev):
            t0 = time.perf_counter()
            _orig_paint(self, ev)
            _PAINTS.append((time.perf_counter() - t0) * 1000.0)

        _cov_cls.paintEvent = _wrap_paint

    def _wrap_qlabel_show(self):
        t0 = time.perf_counter()
        _orig_qlabel_show(self)
        dt = (time.perf_counter() - t0) * 1000.0
        if self.parent() is None and self.width() >= 1000:
            _ev(f"QLabel.show {self.width()}x{self.height()} {dt:.1f}ms")

    def _wrap_build(self):
        _orig_build(self)
        p = getattr(self, "_fs_idle_pix", None)
        if _DUMP and p is not None and not p.isNull():
            try:
                p.save(os.path.join(OUT, f"fs_idle_{_dump_n['fs']}.png"))
                _dump_n["fs"] += 1
            except Exception:         # noqa: BLE001
                pass

    def _wrap_tick(self):
        t0 = time.perf_counter()
        dt = (t0 - _wrap_tick.last) * 1000.0 if _wrap_tick.last else 0.0
        _orig_tick(self)
        t1 = time.perf_counter()
        _wrap_tick.last = t1
        try:
            elapsed = (self._cover_clock.elapsed()
                       if self._cover_clock.isValid() else -1)
            dur = int(self._cover_duration)
            s = {
                "wall": time.time(), "rel": _ms(), "dt": dt,
                "elapsed": elapsed, "dur": dur,
                "t": (elapsed / dur) if dur > 0 else -1,
                "timer_ms": self._cover_timer.interval(),
                "cost_ms": (t1 - t0) * 1000.0,
                "a": None, "a_op": -1.0, "b": None, "b_op": -1.0,
                "bg": None, "bg_op": -1.0,
                "win_op": self.windowOpacity(),
            }
            cov = self._cover_label
            b = self._cover_label_b
            bg = self._cover_bg
            if cov is not None:
                g = cov.geometry()
                s["a"] = (g.x(), g.y(), g.width(), g.height())
                s["a_op"] = cov.windowOpacity()
            if b is not None:
                g = b.geometry()
                s["b"] = (g.x(), g.y(), g.width(), g.height())
                s["b_op"] = b.windowOpacity()
            if bg is not None:
                g = bg.geometry()
                s["bg"] = (g.x(), g.y(), g.width(), g.height())
                s["bg_op"] = bg.windowOpacity()
            _TICKS.append(s)
        except RuntimeError:
            pass

    _wrap_tick.last = 0.0

    def _wrap_begin(self, pixmap, start, with_bg=True, pixmap_b=None, end=None,
                    want_crossfade=True):
        _ev(f"begin_cover with_bg={with_bg} b={'yes' if pixmap_b is not None else 'no'} "
            f"start={start.x()},{start.y()},{start.width()},{start.height()}")
        t0 = time.perf_counter()
        label = _orig_begin(self, pixmap, start, with_bg=with_bg,
                            pixmap_b=pixmap_b, end=end,
                            want_crossfade=want_crossfade)
        cost = (time.perf_counter() - t0) * 1000.0   # 先计时，再落盘
        try:
            if _DUMP:
                for tag, pm in (("A", pixmap), ("B", pixmap_b)):
                    if pm is not None and not pm.isNull():
                        pm.save(os.path.join(
                            OUT, f"cover_{_dump_n['cov']}_{tag}.png"))
            _dump_n["cov"] += 1
        except Exception:             # noqa: BLE001
            pass
        _COVERS.append({"wall": time.time(), "rel": _ms(),
                        "cost_ms": cost,
                        "ok": label is not None,
                        "has_a": pixmap is not None,
                        "has_b": pixmap_b is not None})
        return label

    def _wrap_finish(self, from_tick=False):
        t0 = time.perf_counter()
        _ev(f"finish_cover from_tick={from_tick}")
        out = _orig_finish(self, from_tick=from_tick)
        _ev(f"finish_cover done {(time.perf_counter() - t0) * 1000.0:.1f}ms")
        return out

    MainWindow._on_cover_tick = _wrap_tick
    MainWindow._begin_window_cover = _wrap_begin
    MainWindow._finish_window_cover = _wrap_finish
    MainWindow._build_fs_idle_pix = _wrap_build
    MainWindow._cover_prescale = _wrap_prescale
    _QLabel.show = _wrap_qlabel_show

    # ---------------- 20ms 采样 ----------------
    sampler = QTimer()
    sampler.setInterval(20)

    def _sample():
        s = {"wall": time.time(), "rel": _ms(),
             "dt": (_ms() - _SAMPLES[-1]["rel"]) if _SAMPLES else 0.0}
        try:
            g = window.geometry()
            s.update(w=(g.x(), g.y(), g.width(), g.height()),
                     op=window.windowOpacity(), fs=window.isFullScreen(),
                     act=window.isActiveWindow())
        except RuntimeError:
            s.update(w=None, op=-1.0, fs=False, act=False)
        try:
            s["cover_timer"] = window._cover_timer.isActive()
        except RuntimeError:
            s["cover_timer"] = False
        for key, attr in (("cov", "_cover_label"),
                          ("covb", "_cover_label_b"),
                          ("covbg", "_cover_bg")):
            c = getattr(window, attr, None)
            if c is None:
                s[key] = None
                continue
            try:
                g = c.geometry()
                s[key] = (g.x(), g.y(), g.width(), g.height())
                s[key + "_op"] = c.windowOpacity()
                s[key + "_vis"] = c.isVisible()
            except RuntimeError:
                s[key] = None
        try:
            t = window._win_geo_tween
            s["geo_tween"] = bool(t is not None and t.running)
            s["fs_anim"] = bool(window._fs_animating)
            s["nccalc"] = bool(window._nccalc_preserve)
            s["pending"] = bool(window._cover_opacity_pending)
        except RuntimeError:
            pass
        _SAMPLES.append(s)

    sampler.timeout.connect(_sample)
    sampler.start()

    # ---------------- 三轮点击 ----------------
    def _click_max(k: int):
        _ev(f"round{k}_click_max_call")
        window._toggle_fullscreen()
        _ev(f"round{k}_click_max_ret")

    def _click_restore(k: int):
        _ev(f"round{k}_click_restore_call")
        window._toggle_fullscreen()
        _ev(f"round{k}_click_restore_ret")

    for k in range(_ROUNDS):
        base = _FIRST_CLICK_MS + k * _ROUND_GAP_MS
        QTimer.singleShot(base, lambda k=k: _click_max(k))
        QTimer.singleShot(base + 1000, lambda k=k: _click_restore(k))

    last = _FIRST_CLICK_MS + (_ROUNDS - 1) * _ROUND_GAP_MS + 2400

    def _quit():
        _ev("quit")
        try:
            sampler.stop()
        except Exception:             # noqa: BLE001
            pass
        try:
            with open(os.path.join(OUT, "app_state.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"samples": _SAMPLES, "events": _EVENTS,
                           "ticks": _TICKS, "covers": _COVERS, "paints": _PAINTS,
                           "t0_wall": _T0_WALL}, fh)
            print("STAGE: app_state.json saved", flush=True)
        except Exception as exc:      # noqa: BLE001
            print(f"STAGE: state save failed {exc}", flush=True)
        try:
            window._stop_snap_worker()
        except Exception:             # noqa: BLE001
            pass
        _restore_settings()
        print("STAGE: settings restored", flush=True)
        sys.stdout.flush()
        os._exit(0)

    QTimer.singleShot(last, _quit)
    _wd = threading.Timer(30.0, lambda: os._exit(3))
    _wd.daemon = True
    _wd.start()
    print(f"STAGE: timers scheduled last={last}", flush=True)
    app.exec()
    return 0


_T0_WALL = time.time()


def _restore_settings() -> None:
    from PySide6.QtCore import QSettings
    st = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st.allKeys()) - set(_SNAPSHOT):
        st.remove(k)
    for k, v in _SNAPSHOT.items():
        st.setValue(k, v)
    st.sync()


# --------------------------------------------------------------------- 父进程
def _acquire_lock() -> bool:
    t0 = time.time()
    while True:
        try:
            os.mkdir(LOCK)
            return True
        except FileExistsError:
            if time.time() - t0 > 240:
                print("⛔ 等待 _check/.app_run.lock 超时（240s）", flush=True)
                return False
            time.sleep(1.0)


def _release_lock() -> None:
    try:
        shutil.rmtree(LOCK, ignore_errors=True)
    except Exception:                 # noqa: BLE001
        pass


def record() -> str:
    rec = os.path.join(OUT, "max_restore_60fps.mp4")
    if os.path.exists(rec):
        os.remove(rec)
    total = (_FIRST_CLICK_MS + (_ROUNDS - 1) * _ROUND_GAP_MS + 2400) / 1000.0
    dur = total + 6.5
    ff_start = time.time()
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "ddagrab=framerate=60",
         "-vf", "hwdownload,format=bgra,scale=1600:1000",
         "-t", f"{dur:.1f}",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16",
         rec],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    time.sleep(1.2)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    app = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--app"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           env=env)
    try:
        out, _ = app.communicate(timeout=45)
        for line in out.decode("utf-8", "replace").splitlines():
            print(line)
    except subprocess.TimeoutExpired:
        app.kill()
        print("⛔ app 子进程超时被杀")
    time.sleep(1.0)
    try:
        ff.stdin.write(b"q")
        ff.stdin.close()
    except Exception:                 # noqa: BLE001
        pass
    try:
        ff.wait(timeout=20)
    except subprocess.TimeoutExpired:
        ff.terminate()
    with open(os.path.join(OUT, "ff_start.json"), "w", encoding="utf-8") as fh:
        json.dump({"ff_start_wall": ff_start}, fh)
    print("app rc:", app.returncode)
    return rec


# --------------------------------------------------------------------- 分析
def _read_video(rec: str):
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(rec)
    rows = []
    prev = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        ts = cap.get(cv2.CAP_PROP_POS_MSEC)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        ys, xs = np.where(gray > 40)
        if len(xs) == 0:
            bbox = (-1, -1, 0, 0)
            ratio = 0.0
            roi_mean = 0.0
        else:
            x0, y0 = int(xs.min()), int(ys.min())
            bw, bh = int(xs.max() - x0 + 1), int(ys.max() - y0 + 1)
            bbox = (x0, y0, bw, bh)
            sub = gray[y0:y0 + bh, x0:x0 + bw]
            ratio = float((sub > 40).mean())
            roi_mean = float(sub.mean())
        luma = float(gray.mean())
        d = None
        if prev is not None:
            diff = cv2.absdiff(gray, prev)
            d = {"dluma": luma - float(prev.mean()),
                 "area": float((diff > 25).mean())}
        rows.append({"w": bbox, "ratio": ratio, "ts": ts, "luma": luma,
                     "roi": roi_mean, "d": d})
        prev = gray
    cap.release()
    return rows


def _pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def analyze(rec: str) -> int:
    sp = os.path.join(OUT, "app_state.json")
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as fh:
            data = json.load(fh)
        _SAMPLES[:] = data.get("samples", [])
        _EVENTS[:] = [(float(t), str(x)) for t, x in data.get("events", [])]
        _TICKS[:] = data.get("ticks", [])
        _COVERS[:] = data.get("covers", [])
        _PAINTS[:] = data.get("paints", [])
    ff_start = None
    fp = os.path.join(OUT, "ff_start.json")
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as fh:
            ff_start = json.load(fh).get("ff_start_wall")

    rows = _read_video(rec)
    if not rows:
        print("⛔ 视频读不到帧")
        return 1

    # 时间轴对齐：子进程 wall ↔ 视频 ts；用「首个普通宽帧」校准
    shown_wall = next((t for t, tag in _EVENTS if tag == "window_shown"), None)
    # 首个「普通宽」帧 = 1134 逻辑宽 × 0.5（1600/3200）；且前一帧是纯黑
    # （黑底垫底窗铺满全屏），避免把录制开头的 WorkBuddy 窗口当锚点。
    norm_idx = None
    for i, r in enumerate(rows):
        bw = r["w"][2]
        if 1050 <= bw <= 1220 and (i == 0 or rows[i - 1]["w"][2] == 0):
            norm_idx = i
            break
    if norm_idx is None:
        norm_idx = next((i for i, r in enumerate(rows)
                         if 1050 <= r["w"][2] <= 1220), None)
    # 时间轴对齐：video_ts(帧) − anchor_ts = 子进程相对 window_shown 的毫秒。
    # anchor 优先用「首个普通宽帧」（前一帧纯黑=黑底垫底窗），否则用墙钟差。
    anchor_ts = None
    if norm_idx is not None:
        anchor_ts = rows[norm_idx]["ts"]
    elif ff_start is not None and shown_wall is not None:
        anchor_ts = (shown_wall - ff_start) * 1000.0
    anchor_ts = anchor_ts or 0.0

    def _ct(i):
        return rows[i]["ts"] - anchor_ts

    ev = [(t, tag) for t, tag in _EVENTS]
    ev_ms = [((t - (shown_wall or t)) * 1000.0, tag) for t, tag in ev]

    def _click_times(kind):
        out = []
        for rel, tag in ev_ms:
            if tag == f"round{len(out)}_{kind}_call":
                out.append(rel)
        return out

    clicks_max = _click_times("click_max")
    clicks_res = _click_times("click_restore")
    if _PAINTS:
        print(f"覆盖层 paintEvent: n={len(_PAINTS)} p50={_pct(_PAINTS, 50):.1f}ms "
              f"p95={_pct(_PAINTS, 95):.1f}ms max={max(_PAINTS):.1f}ms")
    print(f"\n=== 视频 {len(rows)} 帧；对齐锚点 video_ts={anchor_ts:.0f}ms；"
          f"点击时刻 rel(ms) max={['%.0f' % x for x in clicks_max]} "
          f"restore={['%.0f' % x for x in clicks_res]} ===")

    # ---------------- 逐帧表 ----------------
    table = []
    for i, r in enumerate(rows):
        ct = _ct(i)
        flags = []
        if r["w"][2] == 0:
            flags.append("BLACK")
        if r["d"] is not None and abs(r["d"]["dluma"]) > 1.5:
            flags.append("DLUMA")
        if r["d"] is not None and r["d"]["area"] > 0.30:
            flags.append("DIFF30")
        if r["luma"] > 150:
            flags.append("WHITE")
        if r["d"] is not None and r["d"]["dluma"] > 8:
            flags.append("LUMA_JUMP_UP")
        if r["d"] is not None and r["d"]["dluma"] < -8:
            flags.append("LUMA_JUMP_DN")
        table.append((i, r["ts"], ct, r["w"], r["ratio"], r["luma"],
                      r["d"]["dluma"] if r["d"] else 0.0,
                      r["d"]["area"] if r["d"] else 0.0, flags))

    # ---------------- 分段：按点击时刻 ----------------
    seg_bounds = []
    for k in range(len(clicks_max)):
        t0 = clicks_max[k]
        t1 = clicks_res[k] if k < len(clicks_res) else t0 + 2000
        seg_bounds.append((f"R{k + 1}-max", t0, t0 + 700))
        seg_bounds.append((f"R{k + 1}-restore", t1, t1 + 700))

    def _seg_of(ct):
        for name, a, b in seg_bounds:
            if a <= ct < b:
                return name
        return "-"

    # ---------------- 每轮覆盖层 tick 统计 ----------------
    print("\n=== 覆盖层 tick（进程内，权威） ===")
    round_reports = []
    # tick 的 wall 换成「相对 window_shown」，才能和点击时刻对齐
    for s in _TICKS:
        s["r"] = (s["wall"] - shown_wall) * 1000.0 if shown_wall else s["rel"]
    # 以 tick 相邻间隔 > 250ms 切段（同一动画内最长停 140ms）
    segs = []
    cur = []
    for s in _TICKS:
        if cur and s["r"] - cur[-1]["r"] > 250:
            segs.append(cur)
            cur = []
        cur.append(s)
    if cur:
        segs.append(cur)
    used = [g for g in segs if len(g) >= 2]
    for j, g in enumerate(used):
        costs = [x.get("cost_ms", 0.0) for x in g]
        dts = [g[i]["r"] - g[i - 1]["r"] for i in range(1, len(g))]
        ws = [x["a"][2] if x["a"] else 0 for x in g]
        dws = [abs(ws[i] - ws[i - 1]) for i in range(1, len(ws))]
        dur = g[0]["dur"]
        _all_clicks = clicks_max + clicks_res
        _click = (min(_all_clicks, key=lambda c: abs(c - g[0]["r"]))
                  if _all_clicks else None)
        first_delay = (g[0]["r"] - _click) if _click is not None else -1
        print(f"  段{j + 1}: ticks={len(g)} 时长窗口={dur}ms "
              f"r={g[0]['r']:.0f}→{g[-1]['r']:.0f} "
              f"点击后首帧={first_delay:.0f}ms "
              f"t={g[0]['t']:.2f}→{g[-1]['t']:.2f} "
              f"几何 {ws[0]}→{ws[-1]} "
              f"tick耗时 p50={_pct(costs, 50):.1f} p95={_pct(costs, 95):.1f} "
              f"max={max(costs):.1f}ms；"
              f"tick间隔 p50={_pct(dts, 50):.1f} p95={_pct(dts, 95):.1f} "
              f"max={max(dts) if dts else 0:.1f}ms；"
              f"单步Δw max={max(dws) if dws else 0}px；"
              f"双影(B层op>0.05)={sum(1 for x in g if x['b_op'] > 0.05)}帧；"
              f"底色层={sum(1 for x in g if x['bg'] is not None)}帧")
        round_reports.append({
            "ticks": len(g), "dur": dur, "w0": ws[0], "w1": ws[-1],
            "cost_p50": _pct(costs, 50), "cost_p95": _pct(costs, 95),
            "cost_max": max(costs),
            "dt_p50": _pct(dts, 50), "dt_p95": _pct(dts, 95),
            "dt_max": max(dts) if dts else 0,
            "dw_max": max(dws) if dws else 0,
            "b_frames": sum(1 for x in g if x["b_op"] > 0.05),
            "first_delay": first_delay,
        })

    # ---------------- 打印逐帧表（只打有事件的帧 + 段内每 2 帧） ----------------
    print("\n=== 点击同步成本（_begin_window_cover，含 _cover_prescale） ===")
    for c in _COVERS:
        cr = (c["wall"] - shown_wall) * 1000.0 if shown_wall else c["rel"]
        print(f"  r={cr:7.0f}ms A={c.get('has_a')} B={c.get('has_b')} "
              f"cost={c['cost_ms']:6.1f}ms")
    print("\n=== 逐帧表（全屏平均亮度/差） ===")
    print("f###  video_ms  child_ms  bbox(WxH@x,y)  content%  luma  Δluma  diff%  seg  flags")
    named = []
    for (i, ts, ct, w, ratio, luma, dl, da, flags) in table:
        seg = _seg_of(ct)
        interesting = bool(flags) or (seg != "-" and i % 2 == 0)
        if not interesting:
            continue
        print(f"f{i:04d} {ts:8.0f} {ct:9.0f}  {w[2]:4d}x{w[3]:4d}@{w[0]:4d},{w[1]:4d}  "
              f"{ratio * 100:6.1f}  {luma:5.1f} {dl:+7.1f} {da * 100:5.1f}  "
              f"{seg:12s} {' '.join(flags)}")
        if flags:
            named.append((i, ct, seg, flags, w[2], luma, dl, da))

    # ---------------- 关键帧导出 ----------------
    import cv2
    want = {n[0] for n in named}
    for segname, a, b in seg_bounds:
        for i, (_, ts, ct, *_rest) in enumerate(table):
            if a <= ct < b:
                want.add(i)
                break
    cap = cv2.VideoCapture(rec)
    idx = 0
    exported = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx in want:
            cv2.imwrite(os.path.join(OUT, f"f{idx:04d}.png"), frame)
            exported += 1
        idx += 1
    cap.release()
    print(f"\n导出关键帧 {exported} 张 → {OUT}")

    # ---------------- 判据：快照内容 / 黑闪区间 / 几何跳变 ----------------
    snap_files = (sorted(glob.glob(os.path.join(OUT, "cover_*.png")))
                  + sorted(glob.glob(os.path.join(OUT, "fs_idle_*.png"))))
    print("\n=== 覆盖层快照内容（luma 低 = 空白/未绘制） ===")
    snap_rows = []
    for f in snap_files:
        im = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
        if im is None:
            continue
        lm = float(im.mean())
        rt = float((im > 40).mean()) * 100.0
        snap_rows.append((os.path.basename(f), im.shape[1], im.shape[0], lm, rt))
        print(f"  {snap_rows[-1][0]:24s} {im.shape[1]}x{im.shape[0]} "
              f"luma={lm:5.2f} >40={rt:5.1f}%")

    print("\n=== 内容塌陷区间（窗口内 luma < 稳定值的 60%，按 bbox 归一） ===")
    flash_runs = []
    for kind, clicks in (("max", clicks_max), ("restore", clicks_res)):
        for k, c in enumerate(clicks):
            base_pts = [rows[i]["roi"] for i in range(len(rows))
                        if c - 400 <= _ct(i) <= c - 40
                        and rows[i]["w"][2] > 0]
            if not base_pts:
                continue
            base = sorted(base_pts)[len(base_pts) // 2]
            thr = max(1.0, base * 0.60)
            pts = [(i, rows[i]["roi"], _ct(i), rows[i]["luma"])
                   for i in range(len(rows))
                   if c - 60 <= _ct(i) <= c + 900 and rows[i]["w"][2] > 0]
            run = []
            for i, roi_v, ct, lu in pts:
                if roi_v < thr:
                    run.append((i, ct, roi_v, lu))
                else:
                    if len(run) >= 2:
                        flash_runs.append((kind, k + 1, run[0], run[-1], base))
                    run = []
            if len(run) >= 2:
                flash_runs.append((kind, k + 1, run[0], run[-1], base))
    for kind, rnd, a, b, base in flash_runs:
        print(f"  {kind} R{rnd}: f{a[0]:04d}..f{b[0]:04d}  "
              f"child {a[1]:.0f}→{b[1]:.0f}ms  持续 {b[1] - a[1]:.0f}ms  "
              f"roi {a[2]:.2f}..{b[2]:.2f}（基准 {base:.2f}）")
    if not flash_runs:
        print("  （无）")

    print("\n=== 视频单帧几何跳变（60fps 采样，逻辑 px≈×2） ===")
    geo_rows = []
    for kind, clicks in (("max", clicks_max), ("restore", clicks_res)):
        for k, c in enumerate(clicks):
            pts = [(rows[i]["w"][2], _ct(i)) for i in range(len(rows))
                   if c - 40 <= _ct(i) <= c + 700 and rows[i]["w"][2] > 0]
            dws = [abs(pts[j][0] - pts[j - 1][0])
                   for j in range(1, len(pts))]
            if dws:
                geo_rows.append((kind, k + 1, max(dws), len(pts)))
                print(f"  {kind} R{k + 1}: {len(pts)} 帧有内容，"
                      f"单帧最大Δw={max(dws)} px（视频尺度）")


    print("\n=== 判定 ===")
    ok = True
    # 空白帧（窗口区域内没内容 = 桌面/黑屏）
    blanks = [n for n in named if "BLACK" in n[3]]
    print(f"1) 黑帧/空白帧：{len(blanks)} 帧 {[(f'f{n[0]:04d}', n[2]) for n in blanks[:10]]}")
    if blanks:
        ok = False
    # 白底
    whites = [n for n in named if "WHITE" in n[3]]
    print(f"2) 白底帧（luma>150）：{len(whites)} 帧")
    if whites:
        ok = False
    # Δluma 突变
    luma_bad = [n for n in named if "LUMA_JUMP_UP" in n[3] or "LUMA_JUMP_DN" in n[3]]
    print(f"3) 亮度跳变 |Δluma|>8：{len(luma_bad)} 帧")
    for n in luma_bad[:12]:
        print(f"   f{n[0]:04d} child={n[1]:.0f}ms seg={n[2]} Δ={n[6]:+.1f} "
              f"bboxW={n[4]} flags={' '.join(n[3])}")
    if luma_bad:
        ok = False
    if flash_runs:
        print(f"3b) 内容塌陷（窗口内 luma<60%基准）区间：{len(flash_runs)} 段 "
              f"（合计 {sum(b[1] - a[1] for _, _, a, b in flash_runs):.0f}ms）")
        ok = False

    # 掉帧：tick 间隔 > 33ms 占比
    if round_reports:
        r = round_reports[0]
        print(f"4) 覆盖层 tick 耗时 p50={r['cost_p50']:.1f}ms "
              f"p95={r['cost_p95']:.1f}ms max={r['cost_max']:.1f}ms；"
              f"tick 间隔 p50={r['dt_p50']:.1f}ms p95={r['dt_p95']:.1f}ms；"
              f"单步Δw max={r['dw_max']}px")
        if r["cost_p50"] > 22:
            print("   ⛔ 单帧合成成本超过 22ms 预算（掉帧主因）")
            ok = False
        if r["dw_max"] > 120:
            print("   ⛔ 单步几何跳变 > 120px（运动不连续）")
            ok = False
    if geo_rows:
        _gmax = max(x[2] for x in geo_rows)
        print(f"5) 视频单帧最大Δw={_gmax}px（逻辑≈×2）")
        if _gmax > 120:
            print("   ⛔ 60fps 画面里仍有 >120px 的单帧跳变（肉眼=瞬移）")
            ok = False

    print("  结论：", "✓ 通过" if ok else "✗ 有可复现问题（见上方点名帧）")

    # 写 markdown
    md = os.path.join(OUT, "report.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("# 最大化/还原 逐帧诊断（3 轮）\n\n")
        fh.write(f"- 视频：`{os.path.basename(rec)}`（{len(rows)} 帧，ddagrab 60fps，"
                 f"缩放到 1600x1000）\n")
        fh.write(f"- 对齐锚点 video_ts：{anchor_ts:.0f}ms\n\n## 覆盖层 tick 分段\n\n")
        fh.write("| 段 | ticks | 时长窗口 | 点击后首帧 | 几何 | tick耗时p50 | p95 | max | "
                 "tick间隔p50 | p95 | 单步Δw | B层可见帧 |\n")
        fh.write("|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for j, r in enumerate(round_reports):
            fh.write(f"| {j + 1} | {r['ticks']} | {r['dur']}ms | "
                     f"{r['first_delay']:.0f}ms | "
                     f"{r['w0']}→{r['w1']} | {r['cost_p50']:.1f} | "
                     f"{r['cost_p95']:.1f} | {r['cost_max']:.1f} | "
                     f"{r['dt_p50']:.1f} | {r['dt_p95']:.1f} | {r['dw_max']} | "
                     f"{r['b_frames']} |\n")
        fh.write("\n## 点击同步成本（_begin_window_cover）\n\n")
        fh.write("| 时刻(ms) | A快照 | B快照 | 同步耗时 |\n|---|---|---|---|\n")
        for c in _COVERS:
            cr = (c["wall"] - shown_wall) * 1000.0 if shown_wall else c["rel"]
            fh.write(f"| {cr:.0f} | {c.get('has_a')} | {c.get('has_b')} | "
                     f"{c['cost_ms']:.1f}ms |\n")
        fh.write("\n## 点名帧\n\n| 帧 | child_ms | 段 | Δluma | diff% | bboxW | flags |\n")
        fh.write("|---|---|---|---|---|---|---|\n")
        for n in named:
            fh.write(f"| f{n[0]:04d} | {n[1]:.0f} | {n[2]} | {n[6]:+.1f} | "
                     f"{n[7] * 100:.1f} | {n[4]} | {' '.join(n[3])} |\n")
        fh.write("\n## 覆盖层快照内容\n\n")
        fh.write("| 文件 | 尺寸 | luma | >40占比 |\n|---|---|---|---|\n")
        for name, sw, sh, lm, rt in snap_rows:
            fh.write(f"| {name} | {sw}x{sh} | {lm:.2f} | {rt:.1f}% |\n")
        fh.write("\n## 内容塌陷区间（窗口内 luma<60%基准）\n\n")
        if flash_runs:
            fh.write("| 方向 | 轮 | 帧段 | child_ms | 持续 | roi |\n")
            fh.write("|---|---|---|---|---|---|\n")
            for kind, rnd, a, b in flash_runs:
                fh.write(f"| {kind} | R{rnd} | f{a[0]:04d}..f{b[0]:04d} | "
                         f"{a[1]:.0f}→{b[1]:.0f} | {b[1] - a[1]:.0f}ms | "
                         f"{a[2]:.2f}..{b[2]:.2f} |\n")
        else:
            fh.write("无\n")
    print(f"报告 → {md}")
    return 0 if ok else 1


# --------------------------------------------------------------------- main
if __name__ == "__main__":
    if "--app" in sys.argv:
        run_app()
        os._exit(0)

    from PySide6.QtCore import QSettings
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    if not _acquire_lock():
        sys.exit(2)
    rc = 1
    try:
        rec = os.path.join(OUT, "max_restore_60fps.mp4")
        if "--analyze-only" in sys.argv and os.path.exists(rec):
            rc = analyze(rec)
        else:
            rc = analyze(record())
    finally:
        try:
            _restore_settings()
            print("[兜底] QSettings 已按父进程快照恢复")
        except Exception as exc:      # noqa: BLE001
            print(f"[warn] QSettings 兜底恢复失败：{exc}")
        _release_lock()
    sys.exit(rc)
