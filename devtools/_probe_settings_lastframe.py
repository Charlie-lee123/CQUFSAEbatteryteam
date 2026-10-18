"""精确记录「打开设置」补间每一步的 t 与快照几何，以及 done 那一刻的快照几何。

用来回答唯一关键问题：**最后一帧快照是不是已经落到 _settings_geom_to**？
若不是（比终点小/偏），换帧时真面板在 b、快照在更小的矩形 → 边缘露出来 = 跳/鬼影。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location(
    "qa", os.path.join(_ROOT, "devtools", "_qa_anim_verdict.py"))
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


def main() -> int:
    app, window, ctx = qa._boot_single()

    log = []
    done_info = {}

    orig_geom = window._on_settings_geometry

    def wrapped_geom(t):
        orig_geom(t)
        try:
            g = window._settings_shot.geometry().getRect()
        except RuntimeError:
            g = None
        log.append((round(float(t), 4), g))

    window._on_settings_geometry = wrapped_geom

    orig_done = window._on_settings_geometry_done

    def wrapped_done():
        try:
            done_info["shot_at_done"] = window._settings_shot.geometry().getRect()
            done_info["geom_to"] = window._settings_geom_to.getRect()
            done_info["geom_from"] = window._settings_geom_from.getRect()
            done_info["margins"] = getattr(window, "_settings_frame_margins", None)
        except RuntimeError:
            pass
        return orig_done()

    window._on_settings_geometry_done = wrapped_done

    window.btn_settings.click()

    deadline = time.perf_counter() + 1.0
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.002)

    print("=" * 70)
    print("geom_from =", done_info.get("geom_from"))
    print("geom_to   =", done_info.get("geom_to"))
    print("margins   =", done_info.get("margins"))
    print("shot at done =", done_info.get("shot_at_done"))
    print("-" * 70)
    print(f"补间步数 = {len(log)}，最后 12 步：")
    for t, g in log[-12:]:
        print(f"  t={t:<8} shot={g}")
    print("-" * 70)
    # 关键判定
    to = done_info.get("geom_to")
    shot_done = done_info.get("shot_at_done")
    if to and shot_done:
        dx = shot_done[0] - to[0]
        dy = shot_done[1] - to[1]
        dw = shot_done[2] - to[2]
        dh = shot_done[3] - to[3]
        print(f"done 时 快照 vs 终点: dx={dx} dy={dy} dw={dw} dh={dh}")
        if dx == 0 and dy == 0 and dw == 0 and dh == 0:
            print("=> 最后一帧快照 == 终点，换帧无几何差")
        else:
            print("=> ⚠ 最后一帧快照 != 终点，换帧会有边缘跳出（左/上/尺寸差）")
    # 最后一次 t 是否 >= 1.0
    if log:
        print(f"最后一次 t 传值 = {log[-1][0]}  (>=1.0 才会精确落位 b)")
    print("=" * 70)

    qa._teardown_single(app, window, ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
