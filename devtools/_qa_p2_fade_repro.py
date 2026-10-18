"""P2 渐隐不跑的 focused 复现：把 _fade_settings_step 的**参数**也记下来。

现象（pix_run10，build 74b00557）：P2 拍到的快照 alpha 全程 1.000，
_finish_settings_swap 那一帧硬切 6553 点；且 _fade_settings_step 的调用
出现在 fade 开始之前（594ms）和结束之后（1229ms）—— 需要看每次调用的
value 实参与 dialog 身份，判别是「另一个 fade 实例的残留」还是「本实例
被 guard 挡住」还是「tween 没推值」。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtWidgets import QApplication  # noqa: E402,F401

import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location(
    "qa", os.path.join(_ROOT, "devtools", "_qa_anim_verdict.py"))
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


def main() -> int:
    app, window, ctx = qa._boot_single()
    log: list[tuple[float, str, object, object]] = []
    t0 = time.perf_counter()

    orig_step = window._fade_settings_step
    orig_start = window._start_settings_swap_fade
    orig_finish = window._finish_settings_swap

    def step(dialog, value):
        log.append(((time.perf_counter() - t0) * 1000.0, "step",
                    value, id(dialog)))
        return orig_step(dialog, value)

    def start(dialog):
        log.append(((time.perf_counter() - t0) * 1000.0, "start",
                    None, id(dialog)))
        return orig_start(dialog)

    def finish(dialog):
        log.append(((time.perf_counter() - t0) * 1000.0, "finish",
                    None, id(dialog)))
        return orig_finish(dialog)

    window._fade_settings_step = step
    window._start_settings_swap_fade = start
    window._finish_settings_swap = finish

    import ui_anim
    print("animations_enabled:", ui_anim.animations_enabled())
    print("frame_interval_ms:", ui_anim.frame_interval_ms())

    # 第一轮：完整开-关（和 P 场景一样）
    qa.land_settings(app, window)
    qa.close_settings(app, window)
    print(f"--- 第一轮（开+关）后 {len(log)} 条 ---")
    for t, kind, val, did in log:
        print(f"  {t:8.1f}ms {kind:6s} value={val} dialog={did & 0xfff:x}")

    log.clear()
    # 第二轮：只开不关（和 P2 场景一样），盯渐隐
    window.btn_settings.click()
    qa.pump_until(app, lambda: (window._settings_swap_target is None
                                and not window._settings_shot.isVisible()),
                  2500)
    qa.pump(app, 0.1)
    print(f"--- 第二轮（只开）{len(log)} 条 ---")
    for t, kind, val, did in log:
        print(f"  {t:8.1f}ms {kind:6s} value={val} dialog={did & 0xfff:x}")
    steps = [v for _t, k, v, _d in log if k == "step"]
    print(f"step 调用 {len(steps)} 次；value 序列: "
          + " ".join(f"{v:.3f}" for v in steps[:20]))
    if steps:
        alphas = [1.0 - v for v in steps]
        print("推得 alpha 序列: " + " ".join(f"{a:.3f}" for a in alphas[:20]))

    # 还原 + 收尾
    window._fade_settings_step = orig_step
    window._start_settings_swap_fade = orig_start
    window._finish_settings_swap = orig_finish
    qa._teardown_single(app, window, ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
