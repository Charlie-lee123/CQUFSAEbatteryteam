"""打印 _grab_settings_frame 里的裁剪基准：frameGeometry / GetWindowRect / dpr。

确认 crop 的符号：GetWindowRect 在 frameGeometry 外侧多少（物理像素）。
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

    info = {}
    import main_window as mw

    orig = window._grab_settings_frame

    def wrapped():
        d = window._settings_dialog
        try:
            dpr = d.devicePixelRatioF()
            frame = d.frameGeometry()
            geo = d.geometry()
            wr = mw._window_rect_physical(int(d.winId()))
            info["dpr"] = dpr
            info["frame"] = frame.getRect()
            info["geo"] = geo.getRect()
            info["window_rect"] = wr
            if wr is not None:
                fx = int(round(frame.x() * dpr))
                fy = int(round(frame.y() * dpr))
                info["frame_x_phys"] = fx
                info["frame_y_phys"] = fy
                info["old_left(window-frame)"] = wr[0] - fx
                info["old_top"] = wr[1] - fy
                info["new_crop(frame-window)"] = fx - wr[0]
                info["new_crop_y"] = fy - wr[1]
                info["want_w"] = int(round(frame.width() * dpr))
                info["want_h"] = int(round(frame.height() * dpr))
                info["window_rect_w"] = wr[2]
                info["window_rect_h"] = wr[3]
        except Exception as exc:  # noqa: BLE001
            info["err"] = repr(exc)
        return orig()

    window._grab_settings_frame = wrapped

    window.btn_settings.click()
    deadline = time.perf_counter() + 0.6
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.002)

    print("=" * 60)
    for k, v in info.items():
        print(f"{k:>22} = {v}")
    print("=" * 60)

    qa._teardown_single(app, window, ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
