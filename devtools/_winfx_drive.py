"""最大化/还原窗口动画 —— 真实运行驱动 + 内部逐帧遥测（只读产品源码，探针不改产品）。

用法（必须独占运行，脚本自带 mkdir 互斥锁）：
  QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_winfx_drive.py \
      --cycles 6 --tag run1

产物：
  _check/_winfx_<tag>.jsonl   内部遥测（每行一个事件，pc=perf_counter，wall=time.time）
  _check/_winfx_<tag>.mkv     ddagrab 屏幕录帧（1600x1000，~85fps）
  _check/_winfx_<tag>.log     ffmpeg 日志（含 showinfo 的每帧 pts_time）

约定：
- 进/出全屏都走真实入口 ``_toggle_fullscreen()``（= 点标题栏中间那颗键）。
- QSettings 全量快照，finally 原样还原（多出来的键删除）；还原在 Qt 对象析构之后。
- 用 ``_check/.app_run.lock`` 目录互斥，拿不到就等（最多 240s）。
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

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "_check"
LOCK = CHECK / ".app_run.lock"

sys.path.insert(0, str(ROOT))

LOG: list[dict] = []
T0PC = time.perf_counter()
T0WALL = time.time()
_cycle = {"i": 0, "dir": ""}


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
    snap = {}
    for key in settings.allKeys():
        snap[key] = settings.value(key)
    return snap


def restore_settings(settings, snap: dict) -> list[str]:
    """把 QSettings 恢复到快照状态，返回被清理掉的多余键。"""
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
    ap.add_argument("--cycles", type=int, default=6)
    ap.add_argument("--tag", default="run1")
    ap.add_argument("--settle", type=float, default=0.0,
                    help="开录前的静置秒数")
    ap.add_argument("--no-video", action="store_true")
    args = ap.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    CHECK.mkdir(exist_ok=True)

    acquire_lock()
    ffmpeg = None
    jsonl_path = CHECK / f"_winfx_{args.tag}.jsonl"
    mkv_path = CHECK / f"_winfx_{args.tag}.mkv"
    log_path = CHECK / f"_winfx_{args.tag}.ffmpeg.log"

    # ---- QSettings 全量快照（注册表备份不可用：reg.exe 被安全策略拦，改成纯内存快照）
    reg_backup = None

    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication

    import ui_anim

    qsettings = QSettings("MimoWorks", "SerialAssistant")
    snap = dump_settings(qsettings)
    print(f"QSettings 快照 {len(snap)} 键")

    app = QApplication(sys.argv[:1])
    app.setApplicationName("SerialAssistant")
    app.setOrganizationName("MimoWorks")

    # 与 main() 一致：anim_fps=120 → display_max_hz()（本机 165）
    import customization
    applied_hz = ui_anim.set_frame_hz(int(customization.get("anim_fps")))
    print(f"frame_hz={ui_anim.frame_hz()} display_max_hz={ui_anim.display_max_hz()} "
          f"applied={applied_hz}")

    from main_window import MainWindow

    win = MainWindow()
    win._skip_startup_anim = False

    # ---------------- 内部遥测包装（只包实例方法，不改源码） ----------------
    def wrap(name: str, hook):
        orig = getattr(win, name, None)
        if orig is None:
            print(f"!! 缺方法 {name}")
            return None

        def inner(*a, **kw):
            return hook(orig, a, kw)
        setattr(win, name, inner)
        return inner

    # ---------------- 覆盖层各操作的耗时分解（定位 45~85ms/帧的元凶） ----------
    COST: dict[str, list[float]] = {}

    def _cost(name: str, t0: float) -> None:
        COST.setdefault(name, []).append((time.perf_counter() - t0) * 1000.0)

    def _wrap_widget(widget, tag: str) -> None:
        if widget is None:
            return
        for meth in ("setGeometry", "setPixmap", "setWindowOpacity",
                     "setScaledContents", "show", "raise_", "hide"):
            orig = getattr(widget, meth, None)
            if orig is None:
                continue
            nm = f"{tag}.{meth}"

            def inner(*a, _o=orig, _n=nm, **kw):
                t0 = time.perf_counter()
                r = _o(*a, **kw)
                _cost(_n, t0)
                return r
            try:
                setattr(widget, meth, inner)
            except (AttributeError, TypeError):
                pass

    def h_begin(orig, a, kw):
        ev("cover_begin_in", with_bg=kw.get("with_bg", True),
           pixmap_b=None if kw.get("pixmap_b") is None else "has",
           start=[a[1].x(), a[1].y(), a[1].width(), a[1].height()]
           if len(a) > 1 else None,
           end=None if kw.get("end") is None
           else [kw["end"].x(), kw["end"].y(), kw["end"].width(),
                 kw["end"].height()])
        t0 = time.perf_counter()
        r = orig(*a, **kw)
        ev("cover_begin_out", ok=r is not None, dur_ms=(time.perf_counter() - t0) * 1e3,
           win_op=win.windowOpacity())
        _wrap_widget(win._cover_label, "A")
        _wrap_widget(win._cover_label_b, "B")
        _wrap_widget(win._cover_bg, "BG")
        return r

    def h_run(orig, a, kw):
        dur = a[3] if len(a) > 3 else kw.get("duration")
        COST.clear()
        r = orig(*a, **kw)
        ev("cover_run", start=[a[1].x(), a[1].y(), a[1].width(), a[1].height()],
           end=[a[2].x(), a[2].y(), a[2].width(), a[2].height()],
           duration=dur, timer_ms=win._cover_timer.interval(),
           frame_hz=ui_anim.frame_hz(), win_op=win.windowOpacity(),
           is_fs=win.isFullScreen())
        ev("cover_run_out", timer_active=win._cover_timer.isActive())
        return r

    def h_tick(orig, a, kw):
        # ⚠️ 先取 elapsed（本帧的 t），再跑 orig 应用几何/透明度，然后记录
        #    —— 记录到的就是**这一帧真正上屏**的状态（可见到下一 tick 为止）。
        el = win._cover_clock.elapsed() if hasattr(win, "_cover_clock") else -1
        t0 = time.perf_counter()
        r = orig(*a, **kw)
        dur_ms = (time.perf_counter() - t0) * 1e3
        lab = win._cover_label
        g = lab.geometry() if lab is not None else None
        blab = win._cover_label_b
        bg = win._cover_bg
        ev("tick", el=el, t=round(min(1.0, el / float(max(1, win._cover_duration))), 4),
           dur_ms=round(dur_ms, 3),
           cost={k: [round(v, 2) for v in vv] for k, vv in COST.items()},
           geo=[g.x(), g.y(), g.width(), g.height()] if g else None,
           op=round(lab.windowOpacity(), 4) if lab is not None else None,
           op_b=round(blab.windowOpacity(), 4) if blab is not None else None,
           bg_geo=[bg.geometry().x(), bg.geometry().y(),
                   bg.geometry().width(), bg.geometry().height()]
           if bg is not None else None,
           bg_op=round(bg.windowOpacity(), 4) if bg is not None else None,
           win_op=round(win.windowOpacity(), 4),
           is_fs=win.isFullScreen(),
           win_geo=[win.geometry().x(), win.geometry().y(),
                    win.geometry().width(), win.geometry().height()])
        return r

    def h_finish(orig, a, kw):
        ev("cover_finish_in", from_tick=kw.get("from_tick", a[0] if a else None),
           win_op=round(win.windowOpacity(), 4), is_fs=win.isFullScreen())
        r = orig(*a, **kw)
        ev("cover_finish_out", win_op=round(win.windowOpacity(), 4),
           is_fs=win.isFullScreen())
        return r

    def h_setopacity(orig, a, kw):
        val = a[0] if a else kw.get("value")
        ev("win_setopacity_in", v=float(val))
        r = orig(*a, **kw)
        ev("win_setopacity_out", v=float(val), real=round(win.windowOpacity(), 4))
        return r

    def h_showfs(orig, a, kw):
        ev("showFullScreen_in", win_op=round(win.windowOpacity(), 4),
           geo=[win.geometry().x(), win.geometry().y(),
                win.geometry().width(), win.geometry().height()])
        r = orig(*a, **kw)
        ev("showFullScreen_out", is_fs=win.isFullScreen(),
           geo=[win.geometry().x(), win.geometry().y(),
                win.geometry().width(), win.geometry().height()])
        return r

    def h_shownormal(orig, a, kw):
        ev("showNormal_in", win_op=round(win.windowOpacity(), 4),
           geo=[win.geometry().x(), win.geometry().y(),
                win.geometry().width(), win.geometry().height()])
        r = orig(*a, **kw)
        ev("showNormal_out", is_fs=win.isFullScreen(),
           geo=[win.geometry().x(), win.geometry().y(),
                win.geometry().width(), win.geometry().height()])
        return r

    for nm, hk in (("_begin_window_cover", h_begin),
                   ("_run_window_cover", h_run),
                   ("_on_cover_tick", h_tick),
                   ("_finish_window_cover", h_finish),
                   ("setWindowOpacity", h_setopacity),
                   ("showFullScreen", h_showfs),
                   ("showNormal", h_shownormal)):
        wrap(nm, hk)

    # ---------------- 5ms 主窗状态采样 ----------------
    samples: list[dict] = []

    def sampler() -> None:
        g = win.geometry()
        samples.append({
            "pc": round((time.perf_counter() - T0PC) * 1000.0, 3),
            "wall": round(time.time() - T0WALL, 4),
            "win_op": round(win.windowOpacity(), 4),
            "fs": win.isFullScreen(),
            "geo": [g.x(), g.y(), g.width(), g.height()],
            "cover": win._cover_label is not None,
            "cover_geo": ([win._cover_label.geometry().x(),
                           win._cover_label.geometry().y(),
                           win._cover_label.geometry().width(),
                           win._cover_label.geometry().height()]
                          if win._cover_label is not None else None),
        })

    win.show()
    win.apply_startup_state()

    state = {"phase": 0, "toggles": 0, "rec": False, "secs": 0.0}
    total_toggles = args.cycles * 2

    def start_recording() -> None:
        nonlocal ffmpeg
        if args.no_video:
            state["rec"] = True
            return
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
            "-f", "lavfi", "-i", "ddagrab=output_idx=0:framerate=120:draw_mouse=0",
            "-vf", "hwdownload,format=bgra,scale=1600:1000:flags=area,showinfo",
            "-c:v", "libx264rgb", "-preset", "ultrafast", "-crf", "14",
            "-pix_fmt", "bgr0", "-fps_mode", "passthrough",
            "-t", str(4 + total_toggles * 1.45 + 3), str(mkv_path),
        ]
        ev("ffmpeg_start", cmd=" ".join(cmd))
        ffmpeg = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                  stderr=open(log_path, "w", encoding="utf-8",
                                              errors="replace"))
        time.sleep(1.2)          # 让 ddagrab 先稳住，别把启动抖动录进来
        state["rec"] = True
        ev("record_ready")

    def do_toggle() -> None:
        if not state["rec"]:
            return
        if state["toggles"] >= total_toggles:
            ev("all_done")
            QTimer.singleShot(600, app.quit)
            return
        i = state["toggles"]
        was_fs = win.isFullScreen()
        direction = "restore" if was_fs else "enter"
        _cycle["i"] = i // 2
        _cycle["dir"] = direction
        ev("toggle", idx=i, cycle=i // 2, dir=direction,
           is_fs=win.isFullScreen(), win_op=round(win.windowOpacity(), 4),
           geo=[win.geometry().x(), win.geometry().y(),
                win.geometry().width(), win.geometry().height()])
        win.activateWindow()
        win.raise_()
        win._toggle_fullscreen()
        state["toggles"] += 1
        QTimer.singleShot(1450, do_toggle)

    def arm() -> None:
        # 等 SnapWorker（1500ms 后启动）+ 首帧 + fs_idle 快照
        ev("armed", fs_idle=win._fs_idle_pix is not None)
        QTimer.singleShot(int(args.settle * 1000), start_timer)

    def start_timer() -> None:
        start_recording()
        QTimer.singleShot(200, do_toggle)

    samp_timer = QTimer(win)
    samp_timer.setInterval(5)
    samp_timer.timeout.connect(sampler)
    samp_timer.start()
    QTimer.singleShot(4200, arm)     # 启动动画 + snap worker 稳定

    t_exit = time.time()
    app.exec()
    samp_timer.stop()
    print(f"事件循环退出（{time.time()-t_exit:.1f}s）")

    if ffmpeg is not None:
        try:
            ffmpeg.wait(timeout=8)
        except subprocess.TimeoutExpired:
            ffmpeg.terminate()
            ffmpeg.wait(timeout=5)

    # ---- 收尾：先销毁 Qt 对象（含各自 QSettings 实例），再还原设置 ----
    try:
        win._stop_snap_worker()
    except Exception:                              # noqa: BLE001
        pass
    try:
        win.close()
    except Exception:                              # noqa: BLE001
        pass
    try:
        win.deleteLater()
    except Exception:                              # noqa: BLE001
        pass
    app.processEvents()
    del win
    gc.collect()

    jsonl_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in LOG) + "\n",
        encoding="utf-8")
    (CHECK / f"_winfx_{args.tag}_samples.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in samples) + "\n",
        encoding="utf-8")

    extra = restore_settings(qsettings, snap)
    del qsettings
    gc.collect()
    q2 = QSettings("MimoWorks", "SerialAssistant")
    after = dump_settings(q2)
    bad = [k for k in snap if after.get(k) != snap[k]]
    print(f"设置还原：删除多余键 {extra}；不一致键 {bad}")
    print(f"遥测 {len(LOG)} 行 / 采样 {len(samples)} 行 → {jsonl_path.name}")
    release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
