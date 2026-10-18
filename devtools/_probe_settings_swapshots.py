"""换帧窗口高分辨率逐帧存图 + 面板左边缘轨迹，用来找「最后一帧往左跳」。

抓 b 区域外扩 PAD，每帧存全分辨率 PNG。左边缘 = 水平中线上下几行的亮度列剖面里
"从主窗背景升到面板"的那一列；若换帧某帧 left_edge_x 突然变小 = 画面往左跳。
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
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location(
    "qa", os.path.join(_ROOT, "devtools", "_qa_anim_verdict.py"))
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)

OUT = os.path.join(_ROOT, "devtools", "_frames_leftjump")
os.makedirs(OUT, exist_ok=True)
_PAD = 36

state = {"rect": None, "idx": 0, "t0": 0.0, "rows": []}


def _left_edge(img: QImage) -> tuple:
    midy = img.height() // 2
    prof = [0.0] * img.width()
    for dy in range(-4, 5):
        y = midy + dy
        if 0 <= y < img.height():
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                prof[x] += 0.3 * c.red() + 0.59 * c.green() + 0.11 * c.blue()
    best_x, best_d = -1, 0.0
    lo = max(1, _PAD - 18)
    hi = min(img.width() - 1, _PAD + 26)
    for x in range(lo, hi):
        d = prof[x] - prof[x - 1]
        if d > best_d:
            best_d, best_x = d, x
    return best_x, round(best_d, 1)


def grab(tag: str) -> None:
    gr = state["rect"]
    if gr is None:
        return
    screen = QApplication.primaryScreen()
    try:
        pix = screen.grabWindow(0, gr.x(), gr.y(), gr.width(), gr.height())
        img = pix.toImage()
    except Exception as exc:  # noqa: BLE001
        print("grab fail", exc)
        return
    idx = state["idx"]
    img.save(os.path.join(OUT, f"s{idx:03d}_{tag}.png"))
    ex, ed = _left_edge(img)
    state["rows"].append({
        "idx": idx, "tag": tag,
        "t_ms": round((time.perf_counter() - state["t0"]) * 1000, 1),
        "left_edge_x": ex, "edge_strength": ed,
    })
    state["idx"] = idx + 1


def main() -> int:
    app, window, ctx = qa._boot_single()
    screen = QApplication.primaryScreen()
    if screen is None:
        print("no screen")
        return 2
    print("dpr =", screen.devicePixelRatio())

    import ui_anim
    ticker = ui_anim._ticker()
    orig_tick = ticker._tick

    def patched_tick():
        orig_tick()
        try:
            if state["rect"] is None:
                w = getattr(window, "_settings_geom_to", None)
                if w is not None and w.width() > 50:
                    state["rect"] = QRect(w.x() - _PAD, w.y() - _PAD,
                                          w.width() + 2 * _PAD,
                                          w.height() + 2 * _PAD)
            grab("tick")
        except Exception:  # noqa: BLE001
            pass

    ticker._tick = patched_tick

    state["t0"] = time.perf_counter()
    window.btn_settings.click()

    deadline = time.perf_counter() + 1.2
    last_evt = 0.0
    while time.perf_counter() < deadline:
        app.processEvents()
        now = time.perf_counter()
        if now - last_evt > 0.006:
            grab("evt")
            last_evt = now
        time.sleep(0.0005)

    ticker._tick = orig_tick

    rows = state["rows"]
    print(f"frames={len(rows)} -> {OUT}")
    print("idx  t_ms   left_edge_x  strength  tag")
    for r in rows:
        print(f"{r['idx']:>3}  {r['t_ms']:>7}  {r['left_edge_x']:>11}  "
              f"{r['edge_strength']:>8}  {r['tag']}")

    qa._teardown_single(app, window, ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
