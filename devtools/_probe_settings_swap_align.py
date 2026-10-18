"""打开设置「最后一帧闪」根因确定性测量：换帧那一刻**快照与真面板是否逐像素对齐**。

证据链（_probe_settings_open_framesync 的 f007_swap.png，480 宽降采样）：
  换帧那一帧：右缘一条白边 + 右上角关闭按钮重影（两份 X，横向错开几像素），
  下一帧起完全干净。=> 真面板与快照在换帧瞬间**错位 1~3px**，露出一截真面板
  的边/按钮 → 肉眼 = "最后一帧闪"。

本探针不再抓屏（抓屏有节拍噪声），直接量几何：
  · 落定后 dialog.frameGeometry() vs _settings_geom_to（快照所画的矩形）
  · _settings_frame_margins（在**屏幕外**量的）与落定后再量一次的差
  · 快照位图尺寸 vs dialog 实际 frame 尺寸
若 frameGeometry != _settings_geom_to（哪怕差 1px），错位根因即坐实。
"""
import os
import sys

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

    # 落定（换帧结束、快照撤走）
    window.btn_settings.click()

    def landed():
        d = window._settings_dialog
        return (d is not None and window._settings_swap_target is None
                and not window._settings_shot.isVisible())
    qa.pump_until(app, landed, 2500)
    qa.pump(app, 0.15)

    dlg = window._settings_dialog
    if dlg is None:
        print("FATAL: 面板没开起来")
        qa._teardown_single(app, window, ctx)
        return 2

    target = window._settings_geom_to
    actual_frame = dlg.frameGeometry()
    dx = actual_frame.x() - target.x()
    dy = actual_frame.y() - target.y()
    dw = actual_frame.width() - target.width()
    dh = actual_frame.height() - target.height()

    margins_off = window._settings_frame_margins
    # 落定后在**屏幕上**再量一次边框
    frame2, rect2 = dlg.frameGeometry(), dlg.geometry()
    margins_now = (rect2.x() - frame2.x(), rect2.y() - frame2.y(),
                   frame2.right() - rect2.right(),
                   frame2.bottom() - rect2.bottom())

    shot_px = window._settings_shot._shot
    print("=== 换帧对齐测量 ===")
    print(f"_settings_geom_to（快照画的矩形） = ({target.x()},{target.y()}) "
          f"{target.width()}x{target.height()}")
    print(f"dialog.frameGeometry()（真面板落点）= "
          f"({actual_frame.x()},{actual_frame.y()}) "
          f"{actual_frame.width()}x{actual_frame.height()}")
    print(f"偏差 Δx={dx} Δy={dy} Δw={dw} Δh={dh}")
    print(f"边框 margins（屏幕外量）= {margins_off}")
    print(f"边框 margins（落定屏幕上量）= {margins_now}")
    if margins_off != margins_now:
        print(">>> 屏幕外量得的边框与屏幕上不一致 —— _place_dialog_frame 用错"
              "margins，真面板落偏。")
    else:
        print("边框 margins 一致。")
    if shot_px is not None and not shot_px.isNull():
        print(f"快照位图尺寸 = {shot_px.width()}x{shot_px.height()} "
              f"(dpr={shot_px.devicePixelRatio()})")
        print(f"真面板 frame 尺寸 = {actual_frame.width()}x"
              f"{actual_frame.height()}")
    if any((dx, dy, dw, dh)):
        print(">>> 真面板与快照矩形**没有**对齐 —— 最后一帧闪根因坐实。")
    else:
        print(">>> 矩形完全对齐；末帧闪另有根因（看 f*_swap.png 的白边/重影）")

    qa._teardown_single(app, window, ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
