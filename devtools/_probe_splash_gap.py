# -*- coding: utf-8 -*-
r"""开屏卡顿定位：用「心跳看门狗」抓出主线程被谁堵住 ~200ms（附 Python 栈）。

背景
----
`splash_perf.log` 实测每次开屏都有两处 **~200~250ms 的大空档**（big_gaps 里
`(0.14~0.17, 210~250)`、`(0.37~0.44, 193~203)`），median 8ms 但 eq_fps 只有
~100 —— 用户「还是感觉帧率不高」就是这两下顿感，不是稳态帧率不够。

做法
----
主线程装 1ms QTimer 心跳；看门狗线程每 2ms 采样。心跳停 > GAP_MS 就记下
**主线程当前 Python 栈**（`sys._current_frames()`），恢复时输出「堵了多久 + 栈」。
比猜快得多：能直接看出是构造、抓屏、字体引擎还是 QPixmap 转换。

⛔ 只读探针：不改产品源码、不打包、不动 发布/ 与 dist*。
⛔ 退出用 app.quit()（正常退），QSettings 前后快照对比并汇报；不写任何设置。
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GAP_MS = 30.0
RUN_MS = 6000

_hb = [0.0]
_gaps: list[tuple[float, list[str]]] = []
_stop = threading.Event()


def _watchdog(main_tid: int) -> None:
    in_gap = False
    start = 0.0
    stack_at = None
    while not _stop.is_set():
        now = time.perf_counter()
        lag = (now - _hb[0]) * 1000.0 if _hb[0] else 0.0
        if lag > GAP_MS:
            if not in_gap:
                in_gap = True
                start = _hb[0] or now
                fr = sys._current_frames().get(main_tid)
                stack_at = (traceback.format_stack(fr)[-20:] if fr else ["<no frame>"])
        elif in_gap:
            in_gap = False
            dur = (_hb[0] - start) * 1000.0
            _gaps.append((dur, stack_at or []))
        time.sleep(0.002)


def main() -> int:
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication

    main_tid = threading.get_ident()

    # ---- app.exec 前装心跳 + 结束定时器 ----
    _orig_exec = QApplication.exec

    def _exec_with_watch(*_a):             # noqa: N802 - 模仿 Qt 命名
        app = QApplication.instance()
        hb_timer = QTimer()
        hb_timer.setTimerType(Qt.TimerType.PreciseTimer)
        hb_timer.setInterval(1)
        hb_timer.timeout.connect(lambda: _hb.__setitem__(0, time.perf_counter()))
        hb_timer.start()
        QTimer.singleShot(RUN_MS, app.quit)
        threading.Thread(target=_watchdog, args=(main_tid,), daemon=True).start()
        return _orig_exec()

    QApplication.exec = _exec_with_watch

    # ---- 顺带量每帧 paint 成本 ----
    import splash_motion
    _paint_ms: list[float] = []
    _paint_gap: list[tuple[float, float]] = []     # (前一次 paint 到这次的墙钟间隔)
    _paint_ts: list[float] = []                    # 每次 paint 的墙钟时刻
    _last_paint = [0.0]
    _orig_paint = splash_motion.MotionRenderer.paint

    def _timed_paint(self, painter, w, h, t):
        _now = time.perf_counter()
        _paint_ts.append(_now)
        if _last_paint[0]:
            _d = (_now - _last_paint[0]) * 1000.0
            if _d > 80.0:
                _paint_gap.append((_d, float(t)))
        _last_paint[0] = _now
        _t0 = time.perf_counter()
        _orig_paint(self, painter, w, h, t)
        _paint_ms.append((time.perf_counter() - _t0) * 1000.0)

    splash_motion.MotionRenderer.paint = _timed_paint

    # ---- 顺带给主窗构造的几个大段落装上秒表（定位 250ms 到底在哪段）----
    import main_window
    _seg: list[tuple[str, float]] = []
    _seg_ts: list[tuple[str, float, float]] = []
    for _nm in ("__init__", "_restore_settings", "_build_ui",
                "_build_config_panel", "_build_receive_area", "_build_send_area",
                "_update_counters", "_set_send_collapsed", "_enable_edge_hover",
                "_tick_clock", "_wire_shortcuts", "_refresh_ports", "_apply_theme",
                "_refresh_vehicle_badge", "_new_card"):
        _o = getattr(main_window.MainWindow, _nm, None)
        if _o is None:
            continue

        def _mk(_nm, _o):
            def _w(self, *a, **kw):
                _t0 = time.perf_counter()
                try:
                    return _o(self, *a, **kw)
                finally:
                    _t1 = time.perf_counter()
                    _seg.append((_nm, (_t1 - _t0) * 1000.0))
                    _seg_ts.append((_nm, _t0, _t1))
            return _w
        setattr(main_window.MainWindow, _nm, _mk(_nm, _o))

    # ---- 顺带量「首次取墨迹」与「主线程字体预热」的真实成本 ----
    import ui_controls
    _ink_ms: list[tuple[str, float]] = []
    for _nm in ("_edit_ink_offset", "_combo_label_ink_offset"):
        _o = getattr(ui_controls, _nm)

        def _mk(_nm, _o):
            def _w(*a, **kw):
                _t0 = time.perf_counter()
                try:
                    return _o(*a, **kw)
                finally:
                    _ink_ms.append((_nm, (time.perf_counter() - _t0) * 1000.0))
            return _w
        setattr(ui_controls, _nm, _mk(_nm, _o))

    import splash_anim
    _warm_ms: list[float] = []
    _ow = getattr(splash_anim, "_warm_font_on_main", None)
    if _ow is not None:
        def _w():
            _t0 = time.perf_counter()
            try:
                return _ow()
            finally:
                _warm_ms.append((time.perf_counter() - _t0) * 1000.0)
        splash_anim._warm_font_on_main = _w

    import main as app_main

    t0 = time.perf_counter()
    try:
        app_main.main()
    except SystemExit:
        pass
    except Exception:                      # noqa: BLE001
        traceback.print_exc()
    wall = time.perf_counter() - t0
    _stop.set()
    time.sleep(0.05)

    if _paint_ms:
        _s = sorted(_paint_ms)
        _n = len(_s)
        print(f"\n=== MotionRenderer.paint 单帧成本（{_n} 帧）===")
        print(f"  median={_s[_n // 2]:.2f}ms  p95={_s[int(_n * 0.95)]:.2f}ms  "
              f"max={_s[-1]:.2f}ms  sum={sum(_s):.0f}ms")
    if _warm_ms:
        print(f"\n=== 主线程字体预热 _warm_font_on_main = {_warm_ms[0]:.1f}ms ===")
    if _ink_ms:
        _top = sorted(_ink_ms, key=lambda x: -x[1])[:6]
        print(f"\n=== 墨迹测量 {len(_ink_ms)} 次，最贵的 6 次 ===")
        for _n, _d in _top:
            print(f"  {_n}: {_d:.1f}ms")
    if _paint_gap:
        print(f"\n=== paint 之间 >80ms 的空档（帧时间轴，秒）===")
        for _d, _t in sorted(_paint_gap, key=lambda x: -x[0])[:6]:
            print(f"  {_d:7.0f} ms  @t={_t:.2f}s")
    if _paint_ts:
        _worst = max(range(1, len(_paint_ts)),
                     key=lambda i: _paint_ts[i] - _paint_ts[i - 1])
        _lo = max(0, _worst - 6)
        print(f"\n=== 最大空档前后逐帧（t=秒, Δ=ms）===")
        for i in range(_lo, min(len(_paint_ts), _worst + 6)):
            _d = (_paint_ts[i] - _paint_ts[i - 1]) * 1000.0 if i else 0.0
            _mark = " <<<" if i == _worst else ""
            print(f"  t={_paint_ts[i]:6.3f}  Δ={_d:7.1f}{_mark}")
    if _seg_ts and _paint_ts:
        print(f"\n=== 构造段时间轴（相对首帧 paint，秒）===")
        for _n, _a, _b in _seg_ts:
            print(f"  {_a - _paint_ts[0]:6.3f} → {_b - _paint_ts[0]:6.3f}"
                  f"  ({( _b - _a) * 1000:6.1f} ms)  {_n}")
    if _seg:
        print(f"\n=== 主窗构造各段实测 ===")
        for _n, _d in _seg:
            print(f"  {_d:7.1f} ms  MainWindow.{_n}")
    print(f"\n=== 主线程卡顿（> {GAP_MS:.0f}ms），总耗时 {wall:.2f}s ===")
    if not _gaps:
        print("无卡顿")
    for dur, stack in sorted(_gaps, key=lambda x: -x[0])[:6]:
        print(f"\n--- 堵 {dur:.0f} ms ---")
        for line in stack:
            print(line.rstrip())
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
