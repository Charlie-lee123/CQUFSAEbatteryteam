"""换帧前后各抓一帧（快照还在 vs 快照已撤），算两帧的水平位移。

这是用户看到的那一下：`_finish_settings_swap` 里 hide 快照的那一刻。
若前后两帧面板水平错开 N 像素，用户就会觉得"整体往左/右闪一下"。

用法：.venv\\Scripts\\python.exe devtools/_probe_settings_swaplr.py
输出：_frames_swaplr/{before,after}.png + 估计的水平/垂直播移
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location(
    "qa", os.path.join(_ROOT, "devtools", "_qa_anim_verdict.py"))
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)

OUT = os.path.join(_ROOT, "devtools", "_frames_swaplr")
os.makedirs(OUT, exist_ok=True)
_PAD = 40

shots = {}
rect_holder = {"r": None}


def _gray(img):
    """缩小到 1/2 的灰度二维数组（纯 python），用于位移估计。"""
    w, h = img.width(), img.height()
    out = []
    for y in range(0, h, 2):
        row = []
        for x in range(0, w, 2):
            c = img.pixelColor(x, y)
            row.append(0.3 * c.red() + 0.59 * c.green() + 0.11 * c.blue())
        out.append(row)
    return out


def _grab(tag):
    gr = rect_holder["r"]
    if gr is None:
        return None
    screen = QApplication.primaryScreen()
    pix = screen.grabWindow(0, gr.x(), gr.y(), gr.width(), gr.height())
    img = pix.toImage()
    img.save(os.path.join(OUT, f"{tag}.png"))
    return _gray(img)


def _ssd(a, b, dx, dy, ys, xs):
    hb = len(b)
    wb = len(b[0])
    tot, n = 0.0, 0
    for y in ys:
        yy = y + dy
        if not (0 <= yy < hb):
            continue
        ra = a[y]
        rb = b[yy]
        for x in xs:
            xx = x + dx
            if 0 <= xx < wb:
                d = ra[x] - rb[xx]
                tot += d * d
                n += 1
    return (tot / n) if n else float("inf")


def _best_shift(a, b, max_shift=30, max_dy=10):
    """估水平 dx + 垂直 dy（after 相对 before）。先粗搜 dx 再细化，最后搜 dy。"""
    ha, wa = len(a), len(a[0])
    hb, wb = len(b), len(b[0])
    ys = list(range(24, min(ha, hb) - 24, 10))
    xs = list(range(24, min(wa, wb) - max_shift - 24, 8))
    coarse = [(_ssd(a, b, dx, 0, ys, xs), dx)
              for dx in range(-max_shift, max_shift + 1, 2)]
    coarse.sort()
    best_dx, best_ssd = coarse[0][1], coarse[0][0]
    for dx in range(best_dx - 2, best_dx + 3):
        s = _ssd(a, b, dx, 0, ys, xs)
        if s < best_ssd:
            best_ssd, best_dx = s, dx
    best_dy = 0
    for dy in range(-max_dy, max_dy + 1):
        s = _ssd(a, b, best_dx, dy, ys, xs)
        if s < best_ssd:
            best_ssd, best_dy = s, dy
    base0 = _ssd(a, b, 0, 0, ys, xs)
    return best_dx, best_dy, best_ssd, base0


def main() -> int:
    app, window, ctx = qa._boot_single()

    orig_finish = window._finish_settings_swap

    def wrapped_finish(dialog):
        # 快照还在：抓 before
        shots["before"] = _grab("before")
        # 记录此刻快照与 dialog 的几何
        try:
            shots["before_geom"] = (window._settings_shot.geometry().getRect(),
                                    dialog.frameGeometry().getRect())
        except RuntimeError:
            shots["before_geom"] = None
        r = orig_finish(dialog)
        # 快照已撤：抓 after
        shots["after"] = _grab("after")
        try:
            shots["after_geom"] = (window._settings_shot.geometry().getRect(),
                                   dialog.frameGeometry().getRect(),
                                   window._settings_shot.isVisible())
        except RuntimeError:
            shots["after_geom"] = None
        return r

    window._finish_settings_swap = wrapped_finish

    # 记录 rect（等 geom_to 就绪）
    import ui_anim
    ticker = ui_anim._ticker()
    orig_tick = ticker._tick

    def patched_tick():
        orig_tick()
        if rect_holder["r"] is None:
            w = getattr(window, "_settings_geom_to", None)
            if w is not None and w.width() > 50:
                rect_holder["r"] = QRect(w.x() - _PAD, w.y() - _PAD,
                                         w.width() + 2 * _PAD,
                                         w.height() + 2 * _PAD)

    ticker._tick = patched_tick

    window.btn_settings.click()
    deadline = time.perf_counter() + 1.0
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.002)
    ticker._tick = orig_tick

    print("rect =", rect_holder["r"].getRect() if rect_holder["r"] else None)
    print("before_geom (shot, dialog) =", shots.get("before_geom"))
    print("after_geom  (shot, dialog, shot_visible) =", shots.get("after_geom"))

    a, b = shots.get("before"), shots.get("after")
    if a and b:
        dx, dy, ssd, base0 = _best_shift(a, b)
        print("=" * 50)
        print(f"最佳位移 dx={dx} dy={dy}  (after 相对 before)  "
              f"ssd={ssd:.1f}  (0,0 时 ssd={base0:.1f})")
        print("=" * 50)
    else:
        print("未抓到 before/after（可能没进 _finish_settings_swap）")

    qa._teardown_single(app, window, ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
