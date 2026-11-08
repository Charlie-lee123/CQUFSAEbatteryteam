"""帧率实证（离屏，不建主窗）：共享 ticker 在给定 frame_hz 下的**真实**节拍。

只读产品代码，不改任何文件；QT_QPA_PLATFORM=offscreen 下跑，不显示窗口。

回答一个问题：设置里写 165，实际每帧隔多久？
（`ui_anim.frame_interval_ms()` 给的是 round(1000/hz)，但 Windows 上 QTimer 有
下限，实际间隔会略大 —— 这里量出来。）

用法：
    python devtools/_audit_ticker_fps.py
"""

from __future__ import annotations

import os
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer                                # noqa: E402
from PySide6.QtWidgets import QApplication                        # noqa: E402

import ui_anim                                                     # noqa: E402


def measure(hz: int, ms: int = 900, min_interval_ms: int = 0) -> dict:
    ui_anim.set_frame_hz(hz)
    app = QApplication.instance() or QApplication([])
    stamps: list[float] = []
    done = {"v": False}

    def on_value(_v) -> None:
        stamps.append(time.perf_counter())

    tw = ui_anim.Tween(ms, ui_anim.out_cubic, on_value,
                       min_interval_ms=min_interval_ms)
    tw.start(0.0, 1.0, ms)

    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(app.quit)
    guard.start(ms + 400)
    tw._on_finished_orig = None
    app.exec()
    guard.stop()

    if len(stamps) < 4:
        return {"hz": hz, "frames": len(stamps), "ok": False}
    deltas = [(b - a) * 1000.0 for a, b in zip(stamps, stamps[1:])]
    span = (stamps[-1] - stamps[0]) * 1000.0
    return {
        "hz": hz,
        "min_interval": min_interval_ms,
        "requested_ms": ui_anim.frame_interval_ms(),
        "ticker_ms": ui_anim.ticker_interval(),
        "frames": len(stamps),
        "median": statistics.median(deltas),
        "mean": statistics.fmean(deltas),
        "p95": sorted(deltas)[max(0, int(len(deltas) * 0.95) - 1)],
        "max": max(deltas),
        "over20ms": sum(1 for d in deltas if d > 20.0),
        "wall_fps": 1000.0 * (len(stamps) - 1) / span if span > 0 else 0.0,
        "ok": True,
    }


def main() -> int:
    app = QApplication.instance() or QApplication([])          # noqa: F841
    # ⚠️ 离屏平台报告 refreshRate()=60，set_frame_hz 会被夹到 60 —— 那样量不到
    #    165/240。这里只在**本进程内**替换上限函数（不改产品代码），用来量出
    #    Qt 定时器的真实下限。
    ui_anim.display_max_hz = lambda: ui_anim.FPS_MAX_CAP   # type: ignore[assignment]
    print(f"platform={os.environ.get('QT_QPA_PLATFORM')} "
          f"cap(已放宽)={ui_anim.display_max_hz()}")
    print(f"{'目标':>6} {'请求ms':>7} {'ticker':>7} {'帧数':>5} "
          f"{'中位ms':>7} {'均值ms':>7} {'P95ms':>7} {'最大ms':>7} "
          f"{'实测fps':>8} {'>20ms帧':>7}")
    for hz in (60, 120, 144, 165, 240):
        r = measure(hz)
        if not r.get("ok"):
            print(f"{hz:>6}  帧数不足({r['frames']})，跳过")
            continue
        print(f"{r['hz']:>6} {r['requested_ms']:>7} {r['ticker_ms']:>7} "
              f"{r['frames']:>5} {r['median']:>7.2f} {r['mean']:>7.2f} "
              f"{r['p95']:>7.2f} {r['max']:>7.2f} {r['wall_fps']:>8.1f} "
              f"{r['over20ms']:>7}")

    # ---- 节流档（min_interval_ms）实测：接收区最大化 / 回放条用的就是这两档 ----
    print()
    print("min_interval_ms 实测（hz=165，时长 260ms，模拟 _rx_transition / _replay_bar）")
    print(f"{'min_int':>7} {'实际节拍ms':>10} {'发值次数':>8} "
          f"{'中位ms':>7} {'P95ms':>7} {'最大ms':>7} {'等效fps':>8} {'>20ms帧':>7}")
    for mini in (0, 16, 12, 8):
        r = measure(165, 260, mini)
        if not r.get("ok"):
            print(f"{mini:>7}  帧数不足({r['frames']})，跳过")
            continue
        print(f"{mini:>7} {r['median']:>10.2f} {r['frames']:>8} "
              f"{r['median']:>7.2f} {r['p95']:>7.2f} {r['max']:>7.2f} "
              f"{r['wall_fps']:>8.1f} {r['over20ms']:>7}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
