# -*- coding: utf-8 -*-
r"""开屏「左移之后文字一点一点显露」卡顿探针（2026-11-03 用户反馈专用）。

用户原话
--------
「还是有卡顿，就是左移之后文字一点一点显露出来的时候发生的卡顿，就这一个位置
非常明显。」

这个探针**不猜**，复刻 `main.py` 的真实启动时序，把每一帧的
墙钟间隔 / hold 次数 / 解码耗时 / 在途预解深度 / 冻结期 worker 利用率全记下来，
然后单独把擦入段（idx 180~215）拎出来看。

复刻的时序（与 `main.py:346-421` 逐行对齐）
------------------------------------------
1. `prewarm_async(16)`            —— 双击后的初始化时间先解前 16 帧
2. `play_configured()`            —— 起开屏，立即返回
3. `still_delay_ms(player)`       —— manifest 里的天然静止段（1900ms）
4. `QTimer.singleShot(delay, …)`：
       `pause_clock()` → 构造真 `MainWindow()`（~1s 阻塞）→ **恢复时钟**
       → `show()` + `singleShot(0, apply_startup_state)`（再阻塞 ~200ms）
5. `app.exec()` 跑完整段动画

⛔ 本探针只读，不改任何业务代码；QSettings 全量快照 → finally 还原。

指标定义
--------
- **帧间隔**：相邻两次**成功显示**（`_show_frame` 返回 True）的墙钟差。
  hold（返回 False）不画新帧，眼睛看到的画面就停在那儿，所以间隔按成功显示算。
- **held**：`_show_frame` 返回 False 的次数（本帧还没解好，保持上一帧）。
- **解码耗时**：`_decode_frame` 的 worker 侧墙钟（含 np.fromfile + imdecode +
  cover resize + BGR→BGRA）。按 idx 归档，用于把"gap 大"对到"哪帧解慢了"。
- **在途深度**：成功显示那一刻 `len(_dec_futs)`，即前方还有多少帧在排/在解。

用法
----
    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_probe_splash_wipe.py
    （探针默认就设 windows；想 headless 对比可显式 QT_QPA_PLATFORM=offscreen）

判据（相对基线）
----------------
擦入段（180~215）最大帧间隔 & held 次数：修复后必须**明显下降**。
全程最大 gap 落在第几帧也一并打印 —— 用来区分"擦入段特有"和"全局时序问题"。
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = Path(__file__).resolve().parents[1]
DEV = Path(__file__).resolve().parent
for _p in (ROOT, DEV):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from PySide6.QtCore import QSettings, QTimer                # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

# 擦入段（用户指的那个位置）：planB 240 帧 / 60fps，1900ms 冻结 = 第 114 帧，
# 解冻后再走 66 帧≈1.1s 到 180 帧；180~215 对应播放 3.0~3.6s。
WIPE_LO, WIPE_HI = 180, 215

# 全程记录容器（worker 线程也会 append，GIL 下 list.append 安全）
_SHOW: list[dict] = []      # 每次 _show_frame 调用
_DEC: dict[int, list] = {}  # idx -> [(t_start, t_end), ...]
_PAINT: list[float] = []    # 每次 paintEvent 的耗时（ms）
_MARK: list[tuple] = []     # ("freeze"/"resume"/"pull", wall)


def _ms(sec: float) -> float:
    return sec * 1000.0


def _pct(vals: list[float], p: float) -> float:
    s = sorted(vals)
    if not s:
        return 0.0
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def _stats(vals: list[float]) -> str:
    if not vals:
        return "（无样本）"
    return (f"n={len(vals)} avg={statistics.mean(vals):.1f} "
            f"median={statistics.median(vals):.1f} "
            f"p95={_pct(vals, 95):.1f} max={max(vals):.1f}")


def _snapshot_settings() -> tuple[QSettings, dict]:
    s = QSettings("MimoWorks", "SerialAssistant")
    return s, {k: s.value(k) for k in s.allKeys()}


def _restore_settings(s: QSettings, snap: dict) -> None:
    for k in list(s.allKeys()):
        if k not in snap:
            s.remove(k)
    for k, v in snap.items():
        s.setValue(k, v)
    s.sync()


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    import app_log
    try:
        app_log.init()
    except Exception:                     # noqa: BLE001
        pass

    settings, snap = _snapshot_settings()
    geo_before = snap.get("geometry")
    max_before = snap.get("custom/startup_maximized")

    # ⛔ A/B 对照：SPLASH_SRC=<某个 splash_anim.py 的副本> 时按该文件加载模块
    #    （同一台机器、同一次会话里前后对比，排除"机器状态变了"的干扰）。
    ab_src = os.environ.get("SPLASH_SRC")
    if ab_src:
        import importlib.util
        spec = importlib.util.spec_from_file_location("splash_anim", ab_src)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["splash_anim"] = mod
        spec.loader.exec_module(mod)
        print(f"[A/B] 用副本加载 splash_anim：{ab_src}")
    import splash_anim
    from splash_anim import _SplashWindow, _PREDEC_WINDOW

    preset = splash_anim.configured()
    print("=" * 78)
    print("开屏「文字擦入段」卡顿探针")
    print("=" * 78)
    print(f"平台        : {os.environ.get('QT_QPA_PLATFORM')}")
    print("prewarm     : 16 帧（同 main.py）")
    print(f"preset      : {preset['id'] if preset else None}")
    print(f"_PREDEC_WINDOW = {_PREDEC_WINDOW}")
    print(f"QSettings   : startup_maximized={max_before}  geometry={geo_before}")
    if preset is None:
        print("[FAIL] 当前配置没有开屏动画（custom/splash_anim=none），无法复现")
        return 1

    fr = _SplashWindow(preset)._frames_for(preset["path"])
    if fr is None:
        print("[FAIL] 不是帧序列路径（走视频回退），本探针只测帧序列")
        return 1
    fps = int(fr["fps"])
    print(f"帧序列      : count={fr['count']} fps={fps} ext={fr['ext']} "
          f"still_at={fr['still_ms']}ms still_len={fr['still_len_ms']}ms")
    print(f"擦入段      : idx {WIPE_LO}~{WIPE_HI} "
          f"（{WIPE_LO / fps:.2f}~{WIPE_HI / fps:.2f}s）")
    from PySide6.QtGui import QGuiApplication
    scr = QGuiApplication.primaryScreen()
    if scr is not None:
        print(f"屏幕        : {scr.size().width()}x{scr.size().height()} "
              f"dpr={scr.devicePixelRatio()} refresh={scr.refreshRate()}Hz")

    # ---- 预热（同 main.py：play_configured 之前）----
    try:
        splash_anim.prewarm_async(16)
    except Exception as exc:              # noqa: BLE001
        print(f"[warn] prewarm_async 失败：{exc}")

    # ---- 仪表：**类级**包 _show_frame / _decode_frame / pause/resume / paintEvent ----
    # ⛔ 必须在 play_configured() 之前挂：start() 里 `_start_frames` 会立刻
    #    `_show_frame(0)` 并提交前 _PREDEC_WINDOW 帧解码，实例级补丁会漏掉这一段。
    #    类级补丁只活在本进程，不落盘、不影响业务代码。
    cls = _SplashWindow
    orig_show, orig_decode = cls._show_frame, cls._decode_frame
    orig_pause, orig_resume = cls.pause_clock, cls.resume_clock
    orig_paint = cls.paintEvent

    def show_hook(self, idx: int) -> bool:
        t0 = time.perf_counter()
        ok = orig_show(self, idx)
        _SHOW.append({"t": t0, "idx": int(idx), "ok": bool(ok),
                      "inflight": len(self._dec_futs or ()),
                      "paused": bool(self._clock_paused)})
        return ok

    def decode_hook(self, idx: int):
        t0 = time.perf_counter()
        out = orig_decode(self, idx)
        _DEC.setdefault(int(idx), []).append((t0, time.perf_counter()))
        return out

    def pause_hook(self) -> None:
        _MARK.append(("freeze", time.perf_counter()))
        return orig_pause(self)

    def resume_hook(self) -> None:
        _MARK.append(("resume", time.perf_counter()))
        return orig_resume(self)

    def paint_hook(self, event) -> None:
        t0 = time.perf_counter()
        orig_paint(self, event)
        _PAINT.append(_ms(time.perf_counter() - t0))

    cls._show_frame = show_hook
    cls._decode_frame = decode_hook
    cls.pause_clock = pause_hook
    cls.resume_clock = resume_hook
    cls.paintEvent = paint_hook

    # ---- 起开屏 ----
    win = splash_anim.play_configured()
    if win is None:
        print("[FAIL] play_configured() 返回 None")
        return 1
    print(f"单帧解码尺寸: {win._pix_size}"
          f"（{win._pix_size[0] * win._pix_size[1] * 4 / 1048576:.1f}MB/帧）")

    # ---- 主窗构造（复刻 main.py._build_window）----
    state = {"win": None}

    def build_window() -> None:
        if state["win"] is not None:
            return
        _MARK.append(("build_begin", time.perf_counter()))
        from main_window import MainWindow
        wnd = MainWindow()
        state["win"] = wnd
        wnd._skip_startup_anim = True
        try:
            win.resume_clock()
        except RuntimeError:
            pass
        _MARK.append(("build_done", time.perf_counter()))
        wnd.show()

        def _apply() -> None:
            _MARK.append(("apply_begin", time.perf_counter()))
            try:
                wnd.apply_startup_state()
            finally:
                _MARK.append(("apply_done", time.perf_counter()))

        QTimer.singleShot(0, _apply)

    def finish_now() -> None:
        app.quit()

    win.set_on_finished(finish_now)
    delay = splash_anim.still_delay_ms(win)
    print(f"still_delay : {delay}ms（到点冻结时钟 + 构造主窗 + 恢复时钟）")
    if delay is None:
        build_window()
    else:
        def at_still() -> None:
            try:
                win.pause_clock()
            except RuntimeError:
                pass
            build_window()
        QTimer.singleShot(max(0, int(delay)), at_still)

    # 兜底：动画 8s 兜底 + 探针自己 12s 硬退
    QTimer.singleShot(12000, app.quit)
    t_loop = time.perf_counter()
    app.exec()
    loop_wall = time.perf_counter() - t_loop

    # ---- 收尾：关窗 + 还原 QSettings ----
    wnd = state["win"]
    if wnd is not None:
        try:
            wnd.close()
        except RuntimeError:
            pass
    try:
        _restore_settings(settings, snap)
    except Exception as exc:              # noqa: BLE001
        print(f"[warn] QSettings 还原失败：{exc}")

    # ================= 出报告 =================
    shown = [e for e in _SHOW if e["ok"]]
    holds = [e for e in _SHOW if not e["ok"]]
    print()
    print("-" * 78)
    print(f"事件循环实际跑了 {loop_wall:.2f}s；成功显示 {len(shown)} 帧；"
          f"hold {len(holds)} 次；paint {len(_PAINT)} 次")
    print("-" * 78)

    marks = {name: t for name, t in _MARK if name in
             ("freeze", "resume", "build_begin", "build_done",
              "apply_begin", "apply_done")}
    if "freeze" in marks and "resume" in marks:
        print(f"冻结段  : {_ms(marks['resume'] - marks['freeze']):.1f}ms"
              f"（pause_clock → resume_clock）")
    if "build_begin" in marks and "build_done" in marks:
        print(f"构造主窗: {_ms(marks['build_done'] - marks['build_begin']):.1f}ms")
    if "apply_begin" in marks and "apply_done" in marks:
        print(f"apply_startup_state: "
              f"{_ms(marks['apply_done'] - marks['apply_begin']):.1f}ms")

    # ---- 冻结期 worker 利用率（关键：预解深度够不够吃满这段冻结）----
    if "freeze" in marks and "resume" in marks:
        ft, rt = marks["freeze"], marks["resume"]
        dur = rt - ft
        in_freeze = [(i, s, e) for i, runs in _DEC.items() for s, e in runs
                     if ft <= s <= rt]
        starts = sorted(s for _i, s, _e in in_freeze)
        ends = sorted(e for _i, _s, e in in_freeze)
        print()
        print(f"【冻结期预解】时长 {_ms(dur):.1f}ms，期间开始的解码 {len(starts)} 帧")
        if starts:
            print(f"  首帧开始 +{_ms(starts[0] - ft):.1f}ms，"
                  f"末帧开始 +{_ms(starts[-1] - ft):.1f}ms，"
                  f"末帧结束 +{_ms(ends[-1] - ft):.1f}ms")
            idle = dur - (ends[-1] - ft)
            print(f"  ⇒ 冻结期最后 {_ms(idle):.1f}ms（{idle / dur * 100:.0f}%）"
                  f"＝ 8 个 worker 全空转")
            waste = int(idle / 0.0167)
            print(f"  ⇒ 折算浪费的预解产能 ≈ {waste} 帧"
                  f"（按 60fps 单帧 16.7ms 计）")
        total_dec = sum(len(v) for v in _DEC.values())
        print(f"  全程解码次数 {total_dec}（每帧都应恰好 1 次；>1 = 重复解码）")
        dup = [i for i, runs in _DEC.items() if len(runs) > 1]
        if dup:
            print(f"  ⚠️ 重复解码的帧 {len(dup)} 个：{sorted(dup)[:20]}")

    # ---- 帧间隔（按成功显示）----
    gaps: list[tuple[int, float]] = []      # (idx, gap_ms)
    for prev, cur in zip(shown, shown[1:]):
        gaps.append((cur["idx"], _ms(cur["t"] - prev["t"])))
    if not gaps:
        print("[FAIL] 没有帧间隔样本")
        return 1

    frame_ms = 1000.0 / fps
    print()
    print(f"【帧间隔】目标单帧 {frame_ms:.2f}ms（{fps}fps）；{_stats([g for _i, g in gaps])}")

    # 分段
    segs = [(0, 60, "起手段"), (60, 120, "中段Ⅰ"), (120, 180, "冻结后段"),
            (WIPE_LO, WIPE_HI + 1, "★擦入段"), (WIPE_HI + 1, 10 ** 9, "收尾段")]
    print()
    print(f"{'分段':<12}{'帧区间':<14}{'样本':<6}{'最大':<10}{'中位':<10}"
          f"{'hold':<6}{'>33ms'}")
    for lo, hi, name in segs:
        g = [x for i, x in gaps if lo <= i <= hi]
        h = [e for e in holds if lo <= e["idx"] <= hi]
        st = sum(1 for x in g if x > 33.0)
        if not g and not h:
            continue
        hi_txt = hi if hi < 10 ** 8 else fr["count"] - 1
        mx = f"{max(g):.1f}" if g else "-"
        md = f"{statistics.median(g):.1f}" if g else "-"
        print(f"{name:<12}{f'{lo}~{hi_txt}':<14}{len(g):<6}{mx:<10}"
              f"{md:<10}{len(h):<6}{st}")

    # ---- 擦入段专判 ----
    wipe_gaps = [x for i, x in gaps if WIPE_LO <= i <= WIPE_HI]
    wipe_holds = [e for e in holds if WIPE_LO <= e["idx"] <= WIPE_HI]
    print()
    print("=" * 78)
    print(f"★ 擦入段（idx {WIPE_LO}~{WIPE_HI}）")
    if wipe_gaps:
        print(f"  显示帧数 {len(wipe_gaps) + 1}   最大帧间隔 {max(wipe_gaps):.1f}ms   "
              f"中位 {statistics.median(wipe_gaps):.1f}ms   "
              f"{_stats(wipe_gaps)}")
        print(f"  >33ms 卡顿帧 {sum(1 for x in wipe_gaps if x > 33.0)} 个")
    else:
        print("  ⚠️ 擦入段没有样本（动画没走到那里？）")
    print(f"  held 次数 {len(wipe_holds)}"
          + (f"  涉及 idx {sorted({e['idx'] for e in wipe_holds})}"
             if wipe_holds else ""))
    print("=" * 78)

    # ---- 全程最大 gap ----
    worst = max(gaps, key=lambda x: x[1])
    print(f"全程最大 gap = {worst[1]:.1f}ms @ 第 {worst[0]} 帧"
          f"（{worst[0] / fps:.2f}s）")
    top = sorted(gaps, key=lambda x: -x[1])[:12]
    print("全程最差 12 帧：" + "  ".join(f"{i}帧:{g:.1f}ms" for i, g in top))

    # ---- 逐帧明细（擦入段前后）----
    dec_ms = {i: statistics.median(_ms(e - s) for s, e in runs)
              for i, runs in _DEC.items()}
    hold_by_idx: dict[int, int] = {}
    for e in holds:
        hold_by_idx[e["idx"]] = hold_by_idx.get(e["idx"], 0) + 1
    print()
    lo_tab = max(0, WIPE_LO - 25)
    hi_tab = min(fr["count"] - 1, WIPE_HI + 15)
    print(f"逐帧明细 idx {lo_tab}~{hi_tab}"
          f"（gap=相邻成功显示的墙钟差，dec=该帧 worker 解码耗时，"
          f"hold=该 idx 被 hold 的次数，q=显示时前方在途帧数）")
    print(f"{'idx':>6}{'gap ms':>10}{'dec ms':>10}{'hold':>7}{'q':>5}  bar")
    gap_by_idx = dict(gaps)
    for e in shown:
        i = e["idx"]
        if not (lo_tab <= i <= hi_tab):
            continue
        g = gap_by_idx.get(i, 0.0)
        bar = "█" * min(60, int(round(g / 2.0)))
        flag = "  <<< 卡顿" if g > 33 else ""
        print(f"{i:>6}{g:>10.1f}{dec_ms.get(i, 0.0):>10.1f}"
              f"{hold_by_idx.get(i, 0):>7}{e['inflight']:>5}  {bar}{flag}")

    # ---- paint 耗时 ----
    if _PAINT:
        print()
        print(f"【paintEvent】{_stats(_PAINT)}")

    # ---- 指纹：改动后必须与源码一致 ----
    import hashlib
    src = (ROOT / "splash_anim.py").read_bytes()
    print()
    print(f"splash_anim.py md5={hashlib.md5(src).hexdigest()} "
          f"sha256={hashlib.sha256(src).hexdigest()}")
    geo_after = QSettings("MimoWorks", "SerialAssistant").value("geometry")
    max_after = QSettings("MimoWorks", "SerialAssistant").value(
        "custom/startup_maximized")
    print(f"QSettings 还原核对：geometry {geo_before} → {geo_after}；"
          f"startup_maximized {max_before} → {max_after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
