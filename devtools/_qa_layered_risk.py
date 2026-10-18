"""分层窗口风险检查（lead 2026-10-19 独立验收任务 ②③ 的**非拍屏**部分）。

背景：P2 83k 尖峰的真因是「非分层窗口 + painter.setOpacity =  multiply 变暗」，
工程师的修法是把快照改走 ``setWindowOpacity``（WS_EX_LAYERED 窗口级合成）。
分层化是 DWM 的窗口属性，天然带三个风险，本探针逐个数：

  ②-a  渐隐全程（alpha 1.0→0）快照必须**真的是** WS_EX_LAYERED —— 否则
        opacity 不生效，直接退回 multiply-dark 老 bug；
  ②-b  渐隐全程快照必须**保持** WS_EX_TOPMOST —— 分层窗口合成路径不同，
        置顶丢了快照会沉到面板底下，底图漏出；
  ②-c  真面板（dialog）与主窗口**全程不得**被分层 —— opacity<1 会让 DWM
        停画阴影，回到 1.0 时阴影"啪"地出现（用户报的最后一帧跳）；
  ②-d  每步的 ``windowOpacity()`` 必须与 ``_alpha`` 一致（set_alpha 真落到
        窗口属性上，不是只改了 Python 字段）；
  ③-a  渐隐中（alpha≈0.5）关面板：补间被当场停、无野 tween、快照复位全不透明、
        主窗口/对话框无分层残留，且再开一次面板渐隐照常工作；
  ③-b  5 次快速开关循环：无分层残留、无野 tween，功能照常。

不覆盖（需要桌面活着拍屏，等 ALIVE 窗口）：dpr=2 位图保真 vs 真面板、渐隐期间
影子可见性、关闭后那帧恢复画面可见性 —— 见 _qa_anim_verdict.py 的 P/P2。

纪律：只包实例属性做旁路观察，跑完逐个还原；不改任何业务代码；窗口几何
还原 (0,0,1134,820)；QSettings 快照还原。
"""

import ctypes
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008


def _exstyle(widget) -> int | None:
    """取窗口扩展样式（拿不到 winId 就返回 None）。"""
    try:
        hwnd = int(widget.winId())
    except (RuntimeError, AttributeError, TypeError):
        return None
    if not hwnd:
        return None
    return int(ctypes.windll.user32.GetWindowLongW(ctypes.c_void_p(hwnd),
                                                   GWL_EXSTYLE))


def _flags(style: int | None) -> str:
    if style is None:
        return "无hwnd"
    out = []
    out.append("LAYERED" if style & WS_EX_LAYERED else "非layered")
    out.append("TOPMOST" if style & WS_EX_TOPMOST else "非topmost")
    return "/".join(out)


def _layered(style: int | None) -> bool:
    return style is not None and bool(style & WS_EX_LAYERED)


def _topmost(style: int | None) -> bool:
    return style is not None and bool(style & WS_EX_TOPMOST)


def pump(app, seconds: float) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.001)


def pump_until(app, predicate, timeout_ms: float = 3000.0) -> bool:
    deadline = time.perf_counter() + timeout_ms / 1000.0
    while time.perf_counter() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.001)
    return predicate()


class FadeTap:
    """包 ``_fade_settings_step`` / ``_finish_settings_swap`` 逐步采窗口样式。

    原方法跑完才采 —— 采到的就是这一步**落定后**的真实窗口属性。
    """

    def __init__(self, window) -> None:
        self.window = window
        self.samples: list[dict] = []
        self.end: dict | None = None
        self.error: str | None = None
        self._saved: list[tuple[str, object]] = []
        for name in ("_fade_settings_step", "_finish_settings_swap"):
            fn = getattr(window, name, None)
            if fn is None:
                self.error = f"main_window 没有 {name}（版本不对？）"
                return
            self._saved.append((name, window.__dict__.get(name)))
            setattr(window, name, self._make_wrapper(fn, name))

    def _make_wrapper(self, original, name: str):
        def wrapper(*args, **kwargs):
            result = original(*args, **kwargs)
            snap = self._sample()
            if name == "_finish_settings_swap":
                self.end = snap
            else:
                self.samples.append(snap)
            return result
        wrapper.__name__ = f"wrapped_{name}"
        return wrapper

    def _sample(self) -> dict:
        w = self.window
        shot = getattr(w, "_settings_shot", None)
        dialog = getattr(w, "_settings_dialog", None)
        snap = {
            "alpha": getattr(shot, "_alpha", None) if shot else None,
            "shot_op": shot.windowOpacity() if shot else None,
            "shot_ex": _exstyle(shot) if shot else None,
            "shot_vis": shot.isVisible() if shot else None,
            "dlg_op": dialog.windowOpacity() if dialog else None,
            "dlg_ex": _exstyle(dialog) if dialog else None,
            "main_ex": _exstyle(w),
            # 收尾那一步（_finish_settings_swap 在 _fade_settings_step 里当场
            # 跑）会把 alpha 复位成 1.0 —— 那是恢复动作不是反弹，逐步判据要排除
            "swap_done": getattr(w, "_settings_swap_target", None) is None,
        }
        return snap

    def uninstall(self) -> None:
        for name, orig in self._saved:
            if orig is None:
                self.window.__dict__.pop(name, None)
            else:
                setattr(self.window, name, orig)


# ---------------------------------------------------------------- 判定辅助

def _check(results, name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}   [{detail}]")


def _run_fade_checks(results, tag: str, samples: list[dict],
                     end: dict | None) -> None:
    """对一次渐隐的逐步样本跑 ②-a~②-d 四条。"""
    # 收尾那一步 swap_done=True（alpha 已复位 1.0），只旁证不收进逐条判据
    fading = [s for s in samples
              if not s["swap_done"] and s["alpha"] is not None
              and s["alpha"] < 0.999]
    _check(results, f"{tag}/渐隐中快照保持 layered（opacity 生效的前提）",
           bool(fading) and all(_layered(s["shot_ex"]) for s in fading),
           f"{len(fading)} 步 alpha<0.999，"
           + ("全部 layered" if fading and all(_layered(s["shot_ex"])
                                              for s in fading)
              else "有步骤丢了 layered（opacity 不生效=退回 multiply-dark）"))
    _check(results, f"{tag}/渐隐中快照保持 TOPMOST（合成路径不丢置顶）",
           bool(fading) and all(_topmost(s["shot_ex"]) for s in fading),
           f"{len(fading)} 步，"
           + ("全部 topmost" if fading and all(_topmost(s["shot_ex"])
                                              for s in fading)
              else "有步骤丢了 topmost（快照会沉到面板底下）"))
    bad_dlg = [s for s in samples
               if _layered(s["dlg_ex"]) or (s["dlg_op"] is not None
                                            and s["dlg_op"] < 0.999)]
    _check(results, f"{tag}/真面板全程不分层且不透明（阴影不被 DWM 停画）",
           not bad_dlg,
           f"{len(samples)} 步面板 opacity 恒 1、无 layered"
           if not bad_dlg else
           f"{len(bad_dlg)} 步面板被分层/半透明（阴影会跳）")
    bad_main = [s for s in samples if _layered(s["main_ex"])]
    _check(results, f"{tag}/主窗口全程不分层", not bad_main,
           "无 layered" if not bad_main else f"{len(bad_main)} 步主窗口被分层")
    # Windows 分层窗口的 alpha 是**字节**（SetLayeredWindowAttributes 收 0~255）：
    # 0.5685 → round(144.97)=145 → 读回 145/255=0.5686，量化台阶 1/255≈0.0039。
    # 所以这里容差必须放到 1/255 以上，否则会把平台物理当成"属性没落地"。
    op_tol = 1.0 / 255.0 + 1e-6
    bad_op = [s for s in samples
              if not s["swap_done"] and s["alpha"] is not None
              and s["shot_op"] is not None
              and abs(s["alpha"] - s["shot_op"]) > op_tol]
    _check(results, f"{tag}/每步 windowOpacity 与 _alpha 一致（属性真落地）",
           not bad_op,
           f"{len(samples)} 步全部一致（Windows 分层 alpha 按字节量化，"
           f"容差 1/255≈0.0039）" if not bad_op
           else "；".join(f"α={s['alpha']:.4f}/op={s['shot_op']:.4f}"
                          for s in bad_op[:3])
           + f"（共 {len(bad_op)} 步，超 1/255 量化台阶）")
    if end is not None:
        ok_end = (not _layered(end["shot_ex"]) and not _layered(end["dlg_ex"])
                  and not _layered(end["main_ex"]))
        _check(results, f"{tag}/收尾无分层残留（快照/面板/主窗口）", ok_end,
               f"快照 {_flags(end['shot_ex'])}；面板 "
               f"{_flags(end['dlg_ex'])}；主窗口 {_flags(end['main_ex'])}")


def _no_wild_tween(window) -> tuple[bool, str]:
    """无野补间 = 设置面板**自己那条时间线**（渐隐 / 几何）没有挂死的。

    不要求整个 ticker 空闲 —— 应用里随时可能有别的合法动画在跑（实测收尾
    那一刻挂着的就是 FlashLabel 的 420ms 一闪，与设置面板无关）。判据：
    ① window 上的两个补间句柄都不是 running；
    ② 挂起队列里没有回调属于设置路径的补间（渐隐 lambda / 几何 step/done）。
    """
    import ui_anim
    fade = getattr(window, "_settings_fade_tween", None)
    geom = getattr(window, "_settings_geom_tween", None)
    running = [name for name, tw in (("fade", fade), ("geom", geom))
               if tw is not None and getattr(tw, "running", False)]
    settings_pending: list[str] = []
    ticker = ui_anim._ticker()
    for tw in getattr(ticker, "_pending", []):
        for fn in (getattr(tw, "_on_value", None),
                   getattr(tw, "_on_finished", None)):
            qual = getattr(fn, "__qualname__", "") or ""
            if any(k in qual for k in ("_start_settings_swap_fade",
                                       "_animate_settings_geometry",
                                       "_fade_settings_step",
                                       "_on_settings_geometry")):
                settings_pending.append(qual)
    ok = not running and not settings_pending
    detail = (f"句柄 running={running or '无'}；"
              f"挂起中的设置路径补间={settings_pending or '无'}")
    return ok, detail


def _boot_single():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))

    from ui_anim import display_max_hz, frame_hz, set_frame_hz
    default_hz = frame_hz()
    set_frame_hz(display_max_hz())

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    from main_window import MainWindow
    window = MainWindow()
    window._port_watch.stop()
    window.setGeometry(0, 0, 1180, 860)
    window.show()
    window.apply_startup_state()
    pump(app, 1.2)
    return app, window, (default_hz, settings, snapshot)


def _teardown_single(app, window, ctx) -> None:
    default_hz, settings, snapshot = ctx
    if window._settings_dialog is not None:
        window._close_settings_dialog(accepted=False)
        pump_until(app, lambda: window._settings_dialog is None, 2000)
    pump(app, 0.3)
    from ui_anim import set_frame_hz
    set_frame_hz(default_hz)
    try:
        if window.isFullScreen():
            window.showNormal()
    except RuntimeError:
        pass
    window.setGeometry(0, 0, 1134, 820)
    window.close()
    for key in set(settings.allKeys()) - set(snapshot):
        settings.remove(key)
    for key, val in snapshot.items():
        settings.setValue(key, val)


def _open_and_wait_landed(app, window, timeout_ms: float = 3000.0) -> bool:
    """点开设置面板，等到换帧渐隐真的在跑（快照可见且 alpha 开始掉）。"""
    window.btn_settings.click()
    return pump_until(
        app,
        lambda: (getattr(getattr(window, "_settings_shot", None),
                         "isVisible", lambda: False)()
                 and (getattr(window, "_settings_shot", None) is not None
                      and getattr(window._settings_shot, "_alpha", 1.0) < 0.999)),
        timeout_ms)


def _wait_swap_done(app, window, timeout_ms: float = 3000.0) -> bool:
    def settled():
        if window._settings_swap_target is not None:
            return False
        try:
            return not window._settings_shot.isVisible()
        except RuntimeError:
            return True
    return pump_until(app, settled, timeout_ms)


def main() -> int:
    app, window, ctx = _boot_single()
    results: list[tuple[str, bool, str]] = []
    try:
        # ---------- A：单次换帧，逐步样式 ----------
        tap = FadeTap(window)
        try:
            if tap.error:
                _check(results, "A/FadeTap 安装", False, tap.error)
            else:
                landed = _open_and_wait_landed(app, window)
                _check(results, "A/换帧渐隐被触发（快照可见且 alpha 开降）",
                       landed,
                       "已进入渐隐" if landed else "等不到渐隐（交互路径变了？）")
                _wait_swap_done(app, window)
                pump(app, 0.1)
                _run_fade_checks(results, "A", tap.samples, tap.end)
                alphas = [s["alpha"] for s in tap.samples
                          if not s["swap_done"] and s["alpha"] is not None]
                mono = all(b <= a + 1e-9
                           for a, b in zip(alphas, alphas[1:]))
                _check(results, "A/alpha 单调不增（1.0→0 的坡，无反弹）", mono,
                       f"{len(alphas)} 步："
                       + " → ".join(f"{a:.2f}" for a in alphas[:8])
                       + (" …" if len(alphas) > 8 else ""))
        finally:
            tap.uninstall()
        # A 收尾必须把面板关上：B/C 都从"面板关着"的干净状态起跑
        if window._settings_dialog is not None:
            window._close_settings_dialog(accepted=False)
            pump_until(app, lambda: window._settings_dialog is None, 2500)
        pump(app, 0.3)

        # ---------- B：渐隐中关面板 ----------
        if window._settings_dialog is not None:      # 防御：从干净状态起跑
            window._close_settings_dialog(accepted=False)
            pump_until(app, lambda: window._settings_dialog is None, 2500)
        tap_b = FadeTap(window)
        try:
            landed = _open_and_wait_landed(app, window)
            _check(results, "B/第二次开面板也能进渐隐（A 后无状态残留）",
                   landed, "已进入渐隐" if landed else "等不到渐隐")
            # 等到 alpha 掉到 ≈0.5 再关（mid-fade）
            mid = pump_until(
                app,
                lambda: (getattr(window._settings_shot, "_alpha", 1.0) <= 0.55
                         and window._settings_dialog is not None),
                2000)
            alpha_at_close = getattr(window._settings_shot, "_alpha", None)
            if window._settings_dialog is not None:
                window._close_settings_dialog(accepted=False)
            closed = pump_until(app,
                                lambda: window._settings_dialog is None, 2500)
            pump(app, 0.4)
            ok_tween, tween_txt = _no_wild_tween(window)
            _check(results, "B/渐隐中关闭：补间当场停、无野 tween",
                   mid and closed and ok_tween,
                   f"关时 alpha={alpha_at_close}；对话框已关={closed}；{tween_txt}")
            _check(results, "B/关闭后快照不可见、无分层残留",
                   not window._settings_shot.isVisible()
                   and not _layered(_exstyle(window._settings_shot))
                   and not _layered(_exstyle(window)),
                   f"快照可见={window._settings_shot.isVisible()}；"
                   f"快照 {_flags(_exstyle(window._settings_shot))}；"
                   f"主窗口 {_flags(_exstyle(window))}")
            _check(results, "B/关闭后 swap 状态清零",
                   window._settings_swap_target is None,
                   f"swap_target={window._settings_swap_target}")
            # 再开一次：渐隐必须照常工作（B 的取消路径没留病根）
            tap_b.samples.clear()
            tap_b.end = None
            landed2 = _open_and_wait_landed(app, window)
            _wait_swap_done(app, window)
            _check(results, "B/取消后再开一次，渐隐照常", landed2,
                   f"再开 {'成功' if landed2 else '失败'}；"
                   f"本次渐隐 {len(tap_b.samples)} 步")
            if tap_b.samples:
                _run_fade_checks(results, "B-重开", tap_b.samples, tap_b.end)
        finally:
            tap_b.uninstall()
        pump(app, 0.3)

        # ---------- C：5 次快速开关循环 ----------
        if window._settings_dialog is not None:      # 防御：从干净状态起跑
            window._close_settings_dialog(accepted=False)
            pump_until(app, lambda: window._settings_dialog is None, 2500)
        cycles_ok = True
        detail = []
        for i in range(5):
            window.btn_settings.click()
            got = pump_until(app,
                             lambda: window._settings_dialog is not None, 1500)
            # 不等待落定，立刻关（大概率落在几何动画或渐隐中段）
            if window._settings_dialog is not None:
                window._close_settings_dialog(accepted=False)
            closed = pump_until(app,
                                lambda: window._settings_dialog is None, 2500)
            pump(app, 0.15)                 # 让收尾那拍的补间彻底熄火再判
            ok_tween, _ = _no_wild_tween(window)
            good = got and closed and ok_tween
            cycles_ok = cycles_ok and good
            detail.append(f"#{i + 1}{'✓' if good else '✗'}")
        pump(app, 0.5)
        ok_tween, tween_txt = _no_wild_tween(window)
        resid = (not _layered(_exstyle(window))
                 and not _layered(_exstyle(window._settings_shot)))
        _check(results, "C/5 次快速开关循环无分层残留、无野 tween",
               cycles_ok and ok_tween and resid,
               f"逐轮 {' '.join(detail)}；{tween_txt}；"
               f"主窗口 {_flags(_exstyle(window))}；"
               f"快照 {_flags(_exstyle(window._settings_shot))}")
        # 循环后功能照常：再完整走一次渐隐
        tap_c = FadeTap(window)
        try:
            landed = _open_and_wait_landed(app, window)
            _wait_swap_done(app, window)
            _check(results, "C/循环后面板功能照常（渐隐可完整跑完）",
                   landed and bool(tap_c.samples),
                   f"渐隐 {len(tap_c.samples)} 步")
            if tap_c.samples:
                _run_fade_checks(results, "C-重开", tap_c.samples, tap_c.end)
        finally:
            tap_c.uninstall()
    finally:
        _teardown_single(app, window, ctx)

    print("\n" + "=" * 78)
    bad = [r for r in results if not r[1]]
    print(f"分层窗口风险检查结论：{len(results) - len(bad)}/{len(results)} PASS")
    for name, _ok, detail in bad:
        print(f"  FAIL  {name}   [{detail}]")
    print("=" * 78)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
