"""测量「打开设置」落定时 —— 真面板 frame 实际落点 vs 快照最终矩形(_settings_geom_to)。

必须泵事件循环（time.sleep 会卡住 ui_anim 定时器，导致补间不前进、对话框停在屏幕外）。
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

    samples = {}
    orig_place = window._place_dialog_frame

    def wrapped_place(dialog, frame_rect):
        r = orig_place(dialog, frame_rect)
        try:
            samples["place_arg"] = frame_rect.getRect()
            samples["place_dlg_frame_immediate"] = dialog.frameGeometry().getRect()
            samples["place_dlg_client_immediate"] = dialog.geometry().getRect()
        except RuntimeError:
            pass
        return r

    window._place_dialog_frame = wrapped_place

    orig_done = window._on_settings_geometry_done

    def wrapped_done():
        try:
            d = window._settings_dialog
            if d is not None:
                samples["done_dlg_frame_immediate"] = d.frameGeometry().getRect()
        except RuntimeError:
            pass
        return orig_done()

    window._on_settings_geometry_done = wrapped_done

    print("main visible:", window.isVisible(),
          "main frame:", window.frameGeometry().getRect())

    window.btn_settings.click()

    # 泵事件循环 ~1.2s（让补间 260ms + swap 50ms 跑完并落定）
    deadline = time.perf_counter() + 1.2
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.002)

    dlg = window._settings_dialog
    if dlg is None:
        print("dialog is None（已关？）")
        qa._teardown_single(app, window, ctx)
        return 1

    fg = dlg.frameGeometry()
    cg = dlg.geometry()
    geom_to = window._settings_geom_to
    margins = getattr(window, "_settings_frame_margins", None)

    print("=" * 64)
    print("dialog.frameGeometry()  =", fg.getRect())
    print("dialog.geometry()       =", cg.getRect())
    print("_settings_geom_to       =", geom_to.getRect())
    print("_settings_frame_margins =", margins)
    print("samples.place_arg       =", samples.get("place_arg"))
    print("samples.place_frame_imm =", samples.get("place_dlg_frame_immediate"))
    print("samples.place_client_im =", samples.get("place_dlg_client_immediate"))
    print("samples.done_frame_immed=", samples.get("done_dlg_frame_immediate"))
    print("dlg.isVisible()         =", dlg.isVisible())
    print("-" * 64)
    print("Δ frame.x - geom_to.x   =", fg.x() - geom_to.x(), "  (负=真面板偏左)")
    print("Δ frame.y - geom_to.y   =", fg.y() - geom_to.y(), "  (负=偏上)")
    print("Δ frame.w - geom_to.w   =", fg.width() - geom_to.width())
    print("Δ frame.h - geom_to.h   =", fg.height() - geom_to.height())
    print("=" * 64)

    qa._teardown_single(app, window, ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
