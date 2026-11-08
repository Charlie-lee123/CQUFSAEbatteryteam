# -*- coding: utf-8 -*-
r"""确认「分屏态点击接收区最大化」走的是哪条路径 + 验证展开态隐藏串口开关文字。

任务（来自 team-lead 的两点反馈）：
  1. 分屏态点「接收区最大化」→ 应当**直接展开成接收区最大化**（清掉分屏接管、
     不再把卡片显示回来），而不是「先回整窗全屏再怎样」。
  2. 展开时开关飞进右侧 rail：只留胶囊图标、**隐藏「串口开关」文字**；回到
     配置卡 home 时恢复文字。普通态（非分屏）行为不得改变。

探针做法：
  - 启动前对 QSettings 做全量快照，finally 里还原（防污染用户配置）。
  - 把窗口摆成分屏（左半屏），调 _apply_snap_zoom 确认接管。
  - 调 _toggle_expand（= 按钮真实入口）触发展开，等动画落定后量状态。
  - 再次 _toggle_expand 触发收起，等落定后量状态。

用法：python devtools\_probe_snap_expand.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QRect, QSettings           # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402

OUT = os.path.join(_ROOT, "_check")

# 启动前对 QSettings 做全量快照，finally 里还原（防污染用户配置）。
_QSETTINGS_SNAPSHOT: dict = {}


def pump(app, seconds: float) -> None:
    """在 seconds 内持续派发事件（让 showFullScreen / 转场补间跑完）。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def settle(app, window, timeout: float = 3.0) -> None:
    """派发事件直到接收区转场补间停下（或超时），确保状态已落定。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if not window._rx_transition_busy():
            # 再多跑几拍，让收尾帧（_rx_finish_*）也落定
            for _ in range(20):
                app.processEvents()
                time.sleep(0.005)
            return
        time.sleep(0.01)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    settings = QSettings("MimoWorks", "SerialAssistant")
    _QSETTINGS_SNAPSHOT.clear()
    _QSETTINGS_SNAPSHOT.update({k: settings.value(k) for k in settings.allKeys()})

    import customization
    from ui_theme import apply_scheme, ui_font
    from main_window import MainWindow

    for spec in customization.specs():
        customization.set(spec["key"], spec["default"])
    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    window = MainWindow()
    window._port_watch.stop()

    screen = app.primaryScreen().availableGeometry()
    # 先以普通窗口位置出现（避免一上来就贴边被误判分屏）
    window.setGeometry(screen.x() + 200, screen.y() + 120, 1280, 860)
    window.show()
    window.raise_()
    pump(app, 0.4)

    # ---- 进入分屏（左半屏） ------------------------------------------------
    # 清掉 _apply_snap_zoom 的 0.35s 落地守卫与状态机残留，确保判定跑得起来。
    window._state_ts = 0.0
    window._expect_normal = False
    window._geo_animating = False
    left = QRect(screen.x(), screen.y(), screen.width() // 2, screen.height())
    window.setGeometry(left)
    window._apply_snap_zoom()
    pump(app, 0.3)

    in_snap = bool(window._snap_zoom)
    cfg_hidden_in_snap = not window._config_card.isVisible()
    print(f"[分屏接管] _snap_zoom={in_snap}  配置卡隐藏={cfg_hidden_in_snap}")
    if not in_snap:
        print("  ✗ 没进成分屏态，无法继续验证路径")
        return 1

    # ---- 分屏态点击「接收区最大化」 -----------------------------------------
    was_snap_before = bool(window._snap_zoom)   # 应为 True
    window._toggle_expand()                     # = 按钮真实入口
    pump(app, 0.4)
    settle(app, window, 3.0)                       # 等展开落定

    expand_from_snap = getattr(window, "_expand_from_snap", False)
    snap_after = bool(window._snap_zoom)
    expanded = bool(window._expanded)
    fs = bool(window.isFullScreen())
    cfg_hidden = not window._config_card.isVisible()
    pre_fs = bool(getattr(window, "_pre_expand_fullscreen", False))
    row_parent = window._open_row.parentWidget()
    row_in_rail = row_parent is window._rx_rail
    open_text = window.btn_open.text()

    print("[展开后]")
    print(f"  _expand_from_snap(was_snap 分支)={expand_from_snap}")
    print(f"  _snap_zoom={snap_after}  _expanded={expanded}  isFullScreen={fs}")
    print(f"  配置卡仍隐藏(直接展开、没先回整窗再藏)={cfg_hidden}")
    print(f"  _pre_expand_fullscreen={pre_fs}")
    print(f"  开关已飞进 rail={row_in_rail}  开关文字={open_text!r}")

    ok_expand = (
        was_snap_before and expand_from_snap and not snap_after
        and expanded and fs and cfg_hidden and pre_fs and row_in_rail
        and open_text == ""
    )

    # ---- 再次点击 → 收起（验证回到整窗全屏 + 文字恢复） --------------------
    window._toggle_expand()
    settle(app, window, 3.0)
    print(f"  [debug] 收起后 _rx_t={getattr(window,'_rx_t',None):.3f} "
          f"_rx_target={getattr(window,'_rx_target',None)} "
          f"busy={window._rx_transition_busy()}")

    collapsed = not bool(window._expanded)
    cfg_back = window._config_card.isVisible()
    snap_still = bool(window._snap_zoom)
    open_text_home = window.btn_open.text()

    print("[收起后]")
    print(f"  _expanded={collapsed}  配置卡回来={cfg_back}  _snap_zoom={snap_still}")
    print(f"  home 态开关文字={open_text_home!r}")

    ok_collapse = (
        collapsed and cfg_back and not snap_still and open_text_home == "串口开关"
    )

    # ---- 普通态（非分屏）行为不被带偏：文字在、配置卡在 --------------------
    plain_text = window.btn_open.text()
    plain_cfg = window._config_card.isVisible()
    ok_plain = plain_text == "串口开关" and plain_cfg

    # ---- 2026-11-03 澄清：普通态展开时开关也必须带文字（动画不变） ----------
    window.showNormal()
    pump(app, 0.3)
    # ⛔ 必须挪到**不贴边**的位置：showNormal 恢复的可能是进分屏前最后
    #    setGeometry 的半屏贴边矩形，那会真实触发 _apply_snap_zoom 接管
    #    （窗口确实处于分屏态，藏文字就是对的，④ 就假挂了）。
    window.setGeometry(screen.x() + 200, screen.y() + 120, 1280, 860)
    pump(app, 0.4)
    window._state_ts = 0.0
    window._expect_normal = False
    window._geo_animating = False
    print(f"[普通态展开前] _snap_zoom={window._snap_zoom}")
    window._toggle_expand()          # 普通窗口直接展开
    pump(app, 0.4)
    settle(app, window, 3.0)
    plain_expand_text = window.btn_open.text()
    window._toggle_expand()          # 收起
    settle(app, window, 3.0)
    plain_home_text = window.btn_open.text()
    ok_plain_expand = (plain_expand_text == "串口开关"
                       and plain_home_text == "串口开关"
                       and not getattr(window, "_rx_switch_textless", False))
    print(f"[普通态展开] 开关文字={plain_expand_text!r} → 收起后={plain_home_text!r}")

    print("\n=== 结论 ===")
    print(f"  ① 分屏态直接展开成接收区最大化：{'✓' if ok_expand else '✗'}")
    print(f"  ② 分屏展开态隐藏「串口开关」文字 / 收起恢复：{'✓' if ok_collapse else '✗'}")
    print(f"  ③ 普通态文字与配置卡不变：{'✓' if ok_plain else '✗'}")
    print(f"  ④ 普通态展开开关带文字（动画不变）：{'✓' if ok_plain_expand else '✗'}")
    return 0 if (ok_expand and ok_collapse and ok_plain and ok_plain_expand) else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        settings = QSettings("MimoWorks", "SerialAssistant")
        # 还原：清掉探针期间写入的键，再写回快照（尽力而为，失败不致命）
        try:
            for k in settings.allKeys():
                settings.remove(k)
            for k, v in _QSETTINGS_SNAPSHOT.items():
                settings.setValue(k, v)
            settings.sync()
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] QSettings 还原失败：{exc}")
    sys.exit(rc)
