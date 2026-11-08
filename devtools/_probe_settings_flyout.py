# -*- coding: utf-8 -*-
r"""验证「设置面板飞入 / 飞出」动画真的在跑（2026-11-07 用户要求改回）。

判据（不看代码、只看几何）：
  - 打开：`_settings_shot` 的矩形从**设置按钮那块**（~30×30）长到面板大小；
  - 关闭：同一容器反向缩回按钮大小，且动画结束后 `_settings_dialog` 归 None。
⛔ 只读探针：不改产品源码、不打包；QSettings 前后快照由 _qa_anim_verdict 负责。
"""
import importlib.util
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

spec = importlib.util.spec_from_file_location(
    "qa", os.path.join(_ROOT, "devtools", "_qa_anim_verdict.py"))
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


def pump(app, ms: float) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.003)


def main() -> int:
    app, window, _ctx = qa._boot_single()
    win = window
    btn = win._settings_button_rect()
    print(f"设置按钮矩形(屏幕坐标) = {btn.width()}x{btn.height()}+{btn.x()}+{btn.y()}")

    win._open_settings()
    open_frames = []
    for _ in range(14):
        pump(app, 22)
        g = win._settings_shot.geometry()
        open_frames.append((round(g.width()), round(g.height())))
    pump(app, 320)
    shot_after = win._settings_shot.geometry()
    dlg = win._settings_dialog
    print(f"打开：快照宽高序列 = {open_frames[:8]} …")
    print(f"打开落定：快照 {shot_after.width()}x{shot_after.height()}，"
          f"面板可见={dlg is not None and dlg.isVisible()}")

    ok_open = (open_frames[0][0] < shot_after.width() * 0.5
               and shot_after.width() > btn.width() * 2
               and dlg is not None)

    win._close_settings_dialog(accepted=False)
    close_frames = []
    for _ in range(14):
        pump(app, 22)
        g = win._settings_shot.geometry()
        close_frames.append((round(g.width()), round(g.height())))
    pump(app, 360)
    print(f"关闭：快照宽高序列 = {close_frames[:8]} …")
    print(f"关闭结束：_settings_dialog = {win._settings_dialog}，"
          f"快照可见={win._settings_shot.isVisible()}")
    ok_close = (close_frames[-1][0] < btn.width() * 2.2
                and win._settings_dialog is None
                and not win._settings_shot.isVisible())

    print(f"[{'PASS' if ok_open else 'FAIL'}] 飞入（从按钮长到面板）")
    print(f"[{'PASS' if ok_close else 'FAIL'}] 飞出（缩回按钮并收场）")
    win.close()
    print("结论：" + ("全部 PASS" if ok_open and ok_close else "有 FAIL"))
    return 0 if (ok_open and ok_close) else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
