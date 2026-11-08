"""开屏「真实停留时间」核验（od47 墙钟驱动修复）。

背景：od46 的 ``_motion_tick`` 用 ``dt = min(wall, 2.5/hz)`` 累加 ``_mt``。
主线程被主窗构造占住时 QTimer 根本不触发，恢复后那 556ms 只推进 15.15ms，
其余 541ms 被永久吞掉 → 真机遥测 frames=320/eq_fps=74.7 = 墙钟 4.28s，
而设计只有 3.0s + 0.3s 淡出，多出 ~1.3s 全是「画面定格」＝用户说的真实停留。

本探针不显示窗口、不碰 QSettings，直接驱动 ``_motion_tick`` 并注入长阻塞，
断言：① 长阻塞后 ``_mt`` ≈ 真实墙钟（而不是只 +15ms）；
      ② 播完 3.0s 动画的墙钟 ≈ 3.0s（而不是 4.3s）。

用法：.venv\\Scripts\\python.exe devtools/_qa_splash_wallclock.py
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QElapsedTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


class FakePlayer:
    """只提供 _motion_tick 需要的字段，不建窗口/不写配置。"""

    def __init__(self) -> None:
        self._done = False
        self._motion = object()          # spec=None 时不会调它的方法
        self._motion_spec = None
        self._motion_total = 3.0
        self._land_ready = False          # 播完不 finish，探针自己收尾
        self._last_wall = time.perf_counter()
        self._mt = 0.0
        self._elapsed = QElapsedTimer()
        self._elapsed.start()
        self._pause_total = 0
        self._perf: dict = {"frames": 0, "intervals": [], "wall_intervals": []}

    def update(self) -> None:
        pass

    _motion_tick = None                  # 下面绑定真实方法


def main() -> int:
    _app = QApplication(sys.argv)
    import splash_anim

    FakePlayer._motion_tick = splash_anim._SplashWindow._motion_tick
    fails = 0

    # ---- A) 单次长阻塞：进度必须跟墙钟，不能被 clamp 吞掉 ----
    f = FakePlayer()
    f._motion_tick()
    t0 = time.perf_counter()
    time.sleep(0.50)                     # 模拟主窗构造 / 首帧合成阻塞
    f._motion_tick()
    wall = time.perf_counter() - t0
    print(f"[A] 注入 {wall*1000:.0f}ms 阻塞后 _mt = {f._mt*1000:.1f}ms")
    print(f"    od46 dt-clamp 旧行为会只有 ~15.2ms（被吞 {(wall-0.015)*1000:.0f}ms）")
    if abs(f._mt - wall) < 0.020:
        print("  [PASS] 进度严格跟墙钟，无时间被吞")
    else:
        print(f"  [FAIL] 进度落后 {abs(f._mt-wall)*1000:.0f}ms")
        fails += 1

    # ---- B) 完整播放：多次长短阻塞混合，总墙钟应 ≈ 3.0s ----
    f = FakePlayer()
    t_start = time.perf_counter()
    f._motion_tick()
    blocks = 0
    while f._mt < f._motion_total:
        time.sleep(0.006)                # 正常 ~6ms 一拍（165Hz）
        f._motion_tick()
        if blocks < 3 and f._mt >= (blocks + 1) * 0.7:
            time.sleep(0.18)             # 3 次 180ms 长阻塞（构造/首帧/DWM）
            f._motion_tick()
            blocks += 1
    total_wall = time.perf_counter() - t_start
    print(f"\n[B] 3 次 180ms 长阻塞 + 正常帧，播完 3.0s 动画的墙钟 = "
          f"{total_wall*1000:.0f}ms")
    print(f"    od46 旧行为约 3.0 + 3*(180-15) = {3000+3*165}ms")
    if total_wall < 3.25:
        print("  [PASS] 总时长未被拉长（旧实现会多出 ~0.5s 定格）")
    else:
        print("  [FAIL] 仍有时间被吞、动画被拉长")
        fails += 1

    print(f"\n==== 结论：{'全部通过 ✓' if fails == 0 else str(fails)+' 项 FAIL ✗'} ====")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
