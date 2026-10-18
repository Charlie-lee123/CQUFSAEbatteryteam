"""打开设置「最后一帧闪」——换帧**瞬间**的确定性全分辨率抓屏。

不再靠轮询碰运气：直接挂钩 `_on_settings_geometry_done`，在它执行**前**、
**后**各抓一次 _settings_geom_to 全分辨率屏幕区域，另抓落定稳态做对照。
这就是用户看到的换帧那一拍（动画最后一帧 → 真面板第一帧）的精确像素。
"""
import os
import sys

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

OUT = os.path.join(_ROOT, "devtools", "_frames_swap_instant")
os.makedirs(OUT, exist_ok=True)
_PAD = 24


def _mean(img: QImage) -> float:
    img = img.convertToFormat(QImage.Format_RGB888)
    tot, n = 0, 0
    for y in range(0, img.height(), 3):
        for x in range(0, img.width(), 3):
            c = img.pixelColor(x, y)
            tot += 0.3 * c.red() + 0.59 * c.green() + 0.11 * c.blue()
            n += 1
    return tot / n if n else float("nan")


def main() -> int:
    app, window, ctx = qa._boot_single()
    screen = QApplication.primaryScreen()

    grabbed = {"before": False, "after": False}
    orig_done = window._on_settings_geometry_done

    def grab(rect_logical: QRect, name: str):
        pix = screen.grabWindow(0, rect_logical.x() - _PAD,
                                rect_logical.y() - _PAD,
                                rect_logical.width() + 2 * _PAD,
                                rect_logical.height() + 2 * _PAD)
        pix.save(os.path.join(OUT, name))
        img = pix.toImage().convertToFormat(QImage.Format_RGB888)
        print(f"{name}: mean_lum={_mean(img):.2f}")

    def done():
        target = getattr(window, "_settings_geom_to", None)
        if target is not None and target.width() > 50 and not grabbed["before"]:
            grabbed["before"] = True
            # 动画最后一帧（快照已到最终矩形、真面板还没 show）
            grab(QRect(target), "0_before_swap.png")
        orig_done()
        if target is not None and target.width() > 50 and not grabbed["after"]:
            grabbed["after"] = True
            # 换帧刚做完（真面板已 show+repaint+dwm_flush，渐隐刚要启动）
            grab(QRect(target), "1_after_swap.png")

    window._on_settings_geometry_done = done
    window.btn_settings.click()
    qa.pump_until(app, lambda: (window._settings_dialog is not None
                                and window._settings_swap_target is None
                                and not window._settings_shot.isVisible()), 2500)
    qa.pump(app, 0.2)
    dlg = window._settings_dialog
    if dlg is not None:
        t = window._settings_geom_to
        grab(QRect(t), "2_settled.png")

    window._on_settings_geometry_done = orig_done
    qa._teardown_single(app, window, ctx)
    print(f"frames -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
