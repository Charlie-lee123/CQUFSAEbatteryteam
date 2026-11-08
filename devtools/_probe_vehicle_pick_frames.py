# -*- coding: utf-8 -*-
r"""探针：**选择车号之后**的动画逐帧体检（用户点名的最高优先项）。

只读研究：不改产品源码。产物写到 `_check/`。

两条采样通道：
  1. ``TickTap`` —— 把取样挂在 ``ui_anim._Ticker._tick`` 上：**补间推进一拍就取一帧**，
     与动画严格同拍（不会因为采样间隔和帧不同步把两帧并成一次大位移）。
     同时记录每一拍的 ``perf_counter`` → 这是动画真实的帧间隔（任务 C）。
  2. ``GapTap`` —— 一条 4ms 的 PreciseTimer，专门盯补间**没在跑**的空档
     （`_schedule_vehicle_relayout` 的 200ms 延迟里没有补间，光靠通道 1 会
     把这段时间当成"漏帧"）。

场景（都是用户在设置 → 车辆里真会做的动作）：
  A1  3 模组 → 5 模组（逐模组行整组增删）
  A2  5 模组 → 3 模组（反方向，收到更少行）
  A3  3 模组 25/25/25 → 3 模组 10/20/30（行数不变，原地改值）
  A4  未选择 → 3 模组（旧三件套整行隐藏）
  A5  走**真下拉弹层**点条目（而不是 setCurrentIndex）—— 验 popup 路径

用法：
  .venv\Scripts\python.exe devtools/_probe_vehicle_pick_frames.py
  .venv\Scripts\python.exe devtools/_probe_vehicle_pick_frames.py --pixels

⛔ 全程 QSettings 快照还原（新增键要删），并开 `_check/.app_run.lock` 互斥，
   避免与其它真实运行并发。
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QPoint, QRect, QSettings, Qt, QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea                 # noqa: E402

CHECK_DIR = os.path.join(_ROOT, "_check")
LOCK_DIR = os.path.join(CHECK_DIR, ".app_run.lock")
OUT_TXT = os.path.join(CHECK_DIR, "vehicle_pick_frames.txt")
OUT_PIX_DIR = os.path.join(CHECK_DIR, "vehicle_pick_pix")
PIXELS = "--pixels" in sys.argv

# 三台车：模组数 3 / 5 / 3（同数不同值）
CAR_A = "E74-2026"
CAR_B = "E75-2026"
CAR_C = "E76-2026"
SPEC = {
    CAR_A: ([25, 25, 25], [8, 8, 8]),
    CAR_B: ([20, 20, 20, 20, 20], [6, 6, 6, 6, 6]),
    CAR_C: ([10, 20, 30], [4, 5, 6]),
}

WIN_W, WIN_H = 1134, 820          # 用户真实窗口尺寸（MEMORY.md）

_lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    _lines.append(text)


# ------------------------------------------------------------------ 互斥锁


def acquire_lock(wait_s: float = 240.0) -> bool:
    os.makedirs(CHECK_DIR, exist_ok=True)
    deadline = time.perf_counter() + wait_s
    while time.perf_counter() < deadline:
        try:
            os.mkdir(LOCK_DIR)
            with open(os.path.join(LOCK_DIR, "owner.txt"), "w", encoding="utf-8") as fh:
                fh.write(f"vehicle-anim pid={os.getpid()}\n")
            return True
        except FileExistsError:
            time.sleep(2.0)
    return False


def release_lock() -> None:
    try:
        for name in os.listdir(LOCK_DIR):
            os.remove(os.path.join(LOCK_DIR, name))
        os.rmdir(LOCK_DIR)
    except OSError:
        pass


def pump(app, ms: float) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


# ------------------------------------------------------------------ 采样


class Frame:
    """一帧状态：被跟踪控件的 global 矩形 + 可见性，加上接收区那几个量。"""

    __slots__ = ("t", "items", "rx", "alpha")

    def __init__(self, t: float, items: dict, rx: tuple, alpha: float = 0.0) -> None:
        self.t = t
        self.items = items
        self.rx = rx
        self.alpha = alpha


class TickTap:
    def __init__(self, app, dialog, window) -> None:
        self.app = app
        self.dialog = dialog
        self.window = window
        self.t0 = time.perf_counter()
        self.action_ms = 0.0
        self.frames: list[Frame] = []
        self.ticks: list[float] = []
        self.gap_frames: list[Frame] = []
        self.gap_active: list[bool] = []
        self._orig_tick = None
        self._timer = None

    # -- 取一帧 --------------------------------------------------------
    def _cache_refs(self) -> None:
        """把稳定的控件引用缓存下来。

        ⛔ 不要在**每一拍**里 findChild(QScrollArea)：那是全树递归搜索，一张 2000px
           高的表单每帧走一遍 ≈ 1~3ms，会把被测的帧率一起拖下去（观察者效应）。
        """
        d = self.dialog
        try:
            scroll = d.findChild(QScrollArea)
            self._bar = scroll.verticalScrollBar() if scroll is not None else None
        except RuntimeError:
            self._bar = None
        self._fixed = {}
        for name, getter in (
                ("panel", lambda: d),
                ("body", lambda: d.form.parentWidget()),
                ("cmb", lambda: d.cmb_vehicle_number),
                ("spin_mod", lambda: d.spin_vehicle_modules),
                ("btn_new", lambda: d.btn_vehicle_new),
                ("btn_export", lambda: d.btn_vehicle_export),
                ("legacy_mod", lambda: d._custom_rows["module_count"][1]),
                ("hint", lambda: d.lbl_vehicle_hint)):
            try:
                self._fixed[name] = getter()
            except (RuntimeError, AttributeError, KeyError):
                self._fixed[name] = None

    def _snapshot(self) -> Frame | None:
        d, w = self.dialog, self.window
        t = (time.perf_counter() - self.t0) * 1000.0
        items: dict[str, tuple] = {}

        def put(name: str, widget) -> None:
            if widget is None:
                items[name] = None
                return
            try:
                if not widget.isVisible():
                    items[name] = None
                    return
                pos = widget.mapToGlobal(QPoint(0, 0))
                size = widget.size()
                items[name] = (pos.x(), pos.y(), size.width(), size.height())
            except RuntimeError:
                items[name] = None

        for name, widget in self._fixed.items():
            put(name, widget)
        try:
            rows = d._module_rows
            put("lbl_mod1", rows[0][0] if rows else None)
            put("row_mod1", rows[0][1] if rows else None)
            put("lbl_last", rows[-1][0] if rows else None)
            put("row_last", rows[-1][1] if rows else None)
            row_count = len(rows)
            shown_cells = list(d._shown_cells)
        except (RuntimeError, IndexError):
            row_count, shown_cells = 0, []

        try:
            overlay = getattr(d, "_rows_fade", None)
            alpha = (round(float(overlay._alpha), 3)
                     if overlay is not None and not overlay.isHidden() else 0.0)
            put("overlay", overlay)
        except (RuntimeError, AttributeError):
            alpha = 0.0

        bar = self._bar
        try:
            scroll_v = bar.value() if bar is not None else -1
            scroll_max = bar.maximum() if bar is not None else -1
        except RuntimeError:
            scroll_v = scroll_max = -1

        module_value = 0
        try:
            module_value = int(d.spin_vehicle_modules.value())
        except (RuntimeError, AttributeError):
            pass

        try:
            box = w.txt_rx
            shot = w._rx_shot
            rx = (box.document().blockCount(),
                  round(float(shot.alpha()), 3),
                  0 if shot.isHidden() else 1,
                  box.viewport().height())
        except (RuntimeError, AttributeError):
            rx = (0, 0.0, 0, 0)
        items["#rows"] = (row_count, scroll_v, scroll_max, module_value)
        items["#cells"] = (len(shown_cells), shown_cells[0] if shown_cells else 0,
                           sum(shown_cells), 0)
        return Frame(t, items, rx, alpha)

    def __enter__(self) -> "TickTap":
        import ui_anim
        self._cache_refs()
        ticker = ui_anim._ticker()
        self._orig_tick = ticker._tick

        def _patched() -> None:
            self._orig_tick()
            self.ticks.append(time.perf_counter())
            frame = self._snapshot()
            if frame is not None:
                self.frames.append(frame)

        ticker._tick = _patched
        # 空档盯梢：补间没跑的时候也要知道布局有没有动
        self._timer = QTimer()
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(4)
        self._timer.timeout.connect(self._gap_sample)
        self._timer.start()
        return self

    def _gap_sample(self) -> None:
        import ui_anim
        try:
            active = ui_anim.ticker_active()
        except Exception:                                   # noqa: BLE001
            active = False
        self.gap_active.append(active)
        if active:
            return                       # 补间在跑：这一帧已由 _tick 记过
        frame = self._snapshot()
        if frame is not None:
            self.gap_frames.append(frame)

    def __exit__(self, *exc) -> bool:
        import ui_anim
        ui_anim._ticker()._tick = self._orig_tick
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        return False

    def run(self, action, settle_ms: float = 1000.0) -> dict:
        self.frames.clear()
        self.ticks.clear()
        self.gap_frames.clear()
        # ⛔ 必须有**动作前**基线帧：重建/显隐是**同步**完成的（在第一拍 tick 之前
        #    就已经改完），没有基线就只能看到"改完之后一直静止"，把整段硬切
        #    误判成"没有变化"。
        base = self._snapshot()
        if base is not None:
            base.t = -1.0
            self.frames.append(base)
        self.t0 = time.perf_counter()
        begin = time.perf_counter()
        action()
        self.action_ms = (time.perf_counter() - begin) * 1000.0
        deadline = time.perf_counter() + settle_ms / 1000.0
        while time.perf_counter() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.app.processEvents()
        return {"frames": list(self.frames),
                "gap": list(self.gap_frames),
                "ticks": list(self.ticks),
                "action_ms": self.action_ms}


# ------------------------------------------------------------------ 分析


def analyse(name: str, rec: dict, hz: int) -> None:
    frames: list[Frame] = rec["frames"]
    gap: list[Frame] = rec["gap"]
    say(f"\n---- [{name}] 采样 {len(frames)} 帧（补间拍，含 1 帧动作前基线）"
        f" + {len(gap)} 帧（空档拍）----")
    say(f"  action() **同步**耗时 = {rec.get('action_ms', 0.0):.2f} ms"
        f"（这一段里界面完全不响应）")
    if len(frames) <= 1:
        say("  ⛔ 除了基线没有任何一帧：这段交互**完全没有补间**在跑")
    # 1) 逐轨迹单帧位移
    names = list(frames[0].items.keys()) if frames else []
    say(f"  {'轨迹':<11}{'位移事件':>8}{'最大单帧位移':>13}{'发生时刻':>11}"
        f"{'异常帧(>12px)':>15}{'显隐翻转':>9}")
    problems: list[str] = []
    for key in names:
        if key.startswith("#"):
            continue
        deltas: list[tuple[float, float, tuple, tuple]] = []
        vis_flip: list[float] = []
        prev = None
        prev_vis = None
        for frame in frames:
            cur = frame.items.get(key)
            cur_vis = cur is not None
            if prev_vis is not None and cur_vis != prev_vis:
                vis_flip.append(round(frame.t, 1))
            prev_vis = cur_vis
            if cur is not None and prev is not None:
                d = max(abs(cur[i] - prev[i]) for i in range(4))
                deltas.append((d, frame.t, prev, cur))
            prev = cur
        if not deltas:
            continue
        values = [d[0] for d in deltas]
        top = max(deltas, key=lambda item: item[0])
        big = [d for d in deltas if d[0] > 12]
        say(f"  {key:<11}{len(deltas):>8}{top[0]:>13.1f}{top[1]:>10.1f}ms"
            f"{len(big):>15}{len(vis_flip):>9}")
        if top[0] > 12:
            say(f"      ↑ 最大位移在 t={top[1]:.1f}ms：{top[2]} → {top[3]}")
        for t in vis_flip:
            say(f"      ↑ 可见性翻转 @ t={t}ms")
        if top[0] > 12 or vis_flip:
            problems.append(key)
    say("  行数/滚动/模组数：" + " ".join(
        f"[t={f.t:.0f} rows={f.items['#rows'][0]} v={f.items['#rows'][1]}"
        f" max={f.items['#rows'][2]} mod={f.items['#rows'][3]}]"
        for f in frames[:: max(1, len(frames) // 6)]))
    say("  逐模组结构 (#cells)：" + " ".join(
        f"[t={f.t:.0f} n={f.items['#cells'][0]} first={f.items['#cells'][1]}"
        f" sum={f.items['#cells'][2]}]"
        for f in frames[:: max(1, len(frames) // 6)]))
    say("  覆盖层 alpha：" + " ".join(
        f"t={f.t:.0f}:{f.alpha:.2f}" for f in frames[:: max(1, len(frames) // 10)]))
    say("  接收区（块数, 旧图 alpha, 旧图可见, viewport 高）：" + " ".join(
        f"t={f.t:.0f}:{f.rx}" for f in frames[:: max(1, len(frames) // 8)]))

    # 2) 关键事件时刻
    def first(match, seq) -> float | None:
        for frame in seq:
            if match(frame):
                return frame.t
        return None

    base_rows = frames[0].items["#rows"][0] if frames else 0
    base_legacy = frames[0].items.get("legacy_mod") is not None if frames else False
    base_blocks = frames[0].rx[0] if frames else 0
    t_rows = first(lambda f: f.items["#rows"][0] != base_rows, frames)
    t_overlay = first(lambda f: f.items.get("overlay") is not None
                      and f.t >= 0, frames)
    t_legacy = first(lambda f: (f.items.get("legacy_mod") is not None) != base_legacy,
                     frames)
    t_blocks = first(lambda f: f.rx[0] != base_blocks, frames)
    t_shot = first(lambda f: f.rx[1] > 0.0 and f.t >= 0, frames)
    events = {"模组行数变化": t_rows, "旧画面覆盖层出现": t_overlay,
              "旧三件套显隐翻转": t_legacy,
              "接收区块数变化": t_blocks, "接收区旧图出现": t_shot}
    say("  关键事件：" + "  ".join(
        f"{key}={'—' if value is None else f'{value:.0f}ms'}"
        for key, value in events.items()))

    # 3) 帧率（任务 C）
    ticks = rec["ticks"]
    if len(ticks) >= 3:
        intervals = [(b - a) * 1000.0 for a, b in zip(ticks, ticks[1:])]
        nominal = 1000.0 / max(1, hz)
        # 补间 idle 造成的长空档：单独算，不算掉帧（用 3×名义间隔 + 5ms 判）
        live = [iv for iv in intervals if iv <= max(3 * nominal, 40.0)]
        idle = [iv for iv in intervals if iv > max(3 * nominal, 40.0)]
        live.sort()
        p50 = statistics.median(live) if live else 0.0
        p95 = live[int(len(live) * 0.95) - 1] if live else 0.0
        dropped = sum(1 for iv in live if iv > 1.5 * nominal)
        say(f"  帧间隔：名义 {nominal:.2f}ms（{hz}Hz）  P50={p50:.2f}  "
            f"P95={p95:.2f}  最大={max(live) if live else 0:.2f}  "
            f"掉帧(>1.5×)={dropped}/{len(live)}  "
            f"无补间空档={len(idle)} 段(合计 {sum(idle):.0f}ms)")
        say(f"  实测有效帧率 = {1000.0 / p50:.1f} fps（中位）")
    say(f"  被判有问题的轨迹：{problems or '无'}")


# ------------------------------------------------------------------ 像素录制


class PixelTap:
    """按 ~60Hz 抓设置面板 body 的**可视区**，用于看残影/双字/闪白。"""

    def __init__(self, app, dialog) -> None:
        self.app = app
        self.dialog = dialog
        self.t0 = 0.0
        self.frames: list[tuple[float, bytes, tuple]] = []
        self.costs: list[float] = []
        self.errors: list[str] = []
        self._timer = None
        self.region: QRect | None = None

    def _grab(self) -> None:
        dialog = self.dialog
        try:
            body = dialog.form.parentWidget()
            scroll = dialog.findChild(QScrollArea)
            bar = scroll.verticalScrollBar()
            view_h = scroll.viewport().height()
            region = QRect(0, bar.value(), body.width(), view_h) & body.rect()
            if region.isEmpty():
                return
            t = (time.perf_counter() - self.t0) * 1000.0
            begin = time.perf_counter()
            pix = body.grab(region)
            cost = (time.perf_counter() - begin) * 1000.0
            if pix.isNull():
                return
            from PySide6.QtGui import QImage
            image = pix.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
            data = bytes(image.constBits())
        except Exception as exc:                        # noqa: BLE001 - 报告一次就够
            self.errors.append(repr(exc))
            return
        self.costs.append(cost)
        self.frames.append((t, data, (region.x(), region.y(),
                                      region.width(), region.height())))

    def __enter__(self) -> "PixelTap":
        self.errors = []
        self._timer = QTimer()
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._grab)
        self._timer.start()
        return self

    def __exit__(self, *exc) -> bool:
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        return False

    def run(self, action, settle_ms: float = 1000.0) -> list:
        self.frames.clear()
        self.costs.clear()
        self.t0 = time.perf_counter()
        action()
        deadline = time.perf_counter() + settle_ms / 1000.0
        while time.perf_counter() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.app.processEvents()
        return list(self.frames)


def pixel_report(name: str, frames: list, tag: str) -> None:
    if len(frames) < 3:
        say(f"\n[像素 {name}] ⛔ 只抓到 {len(frames)} 帧")
        return
    w, h = frames[0][2][2], frames[0][2][3]
    say(f"\n---- [像素 {name}] {len(frames)} 帧，区域 {w}x{h} ----")
    # 与**落定帧**（最后一帧）比：残影/中间态会让整块差异远高于邻近帧
    final = frames[-1][1]
    prev = None
    say(f"  {'t(ms)':>7}{'对上一帧变化点':>15}{'对落定帧差异点':>15}{'差异比':>8}")
    ratios = []
    for t, data, _region in frames:
        d_prev = sum(1 for a, b in zip(data, prev) if abs(a - b) > 6) if prev else 0
        d_final = sum(1 for a, b in zip(data, final) if abs(a - b) > 6)
        ratio = (d_final / max(1, d_prev)) if prev else 0
        ratios.append(ratio)
        say(f"  {t:7.1f}{d_prev:>15}{d_final:>15}{ratio:>8.2f}")
        prev = data
    # 存 6 张关键时刻的图（我肉眼复核用）
    os.makedirs(OUT_PIX_DIR, exist_ok=True)
    from PySide6.QtGui import QImage
    picks = [0, 1, 2, len(frames) // 4, len(frames) // 2, len(frames) - 2]
    for index in sorted({max(0, min(len(frames) - 1, p)) for p in picks}):
        t, data, (rx, ry, rw, rh) = frames[index]
        image = QImage(data, rw, rh, rw, QImage.Format.Format_Grayscale8).copy()
        path = os.path.join(OUT_PIX_DIR, f"{tag}_f{index:03d}_t{t:.0f}ms.png")
        image.save(path)
    say(f"  帧图已存 {OUT_PIX_DIR}（{len(picks)} 张）")


# ------------------------------------------------------------------ 主流程


def build_vehicles() -> None:
    import customization
    for name in list(customization.vehicle_names()):
        customization.delete_vehicle(name)
    for name, (cells, temps) in SPEC.items():
        customization.save_vehicle(name, cells, temps)
    customization.select_vehicle(CAR_A)


def scroll_to_vehicle(dialog, app) -> None:
    from PySide6.QtWidgets import QLabel
    scroll = dialog.findChild(QScrollArea)
    if scroll is None:
        return
    caption = next((label for label in dialog.findChildren(QLabel)
                    if label.text().startswith("车辆（电池箱）")), None)
    if caption is not None:
        scroll.ensureWidgetVisible(caption, 0, 8)
    pump(app, 260)


def combo_index(dialog, name: str) -> int:
    return dialog.cmb_vehicle_number.findData(name)


def click_popup_item(combo, index: int) -> bool:
    """真下拉弹层路径：展开 → 在视图里点那一条（用户实际动作）。"""
    from PySide6.QtTest import QTest
    combo.showPopup()
    view = combo.view()
    if view is None:
        return False
    rect = view.visualRect(combo.model().index(index, 0))
    if not rect.isValid():
        combo.hidePopup()
        return False
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, rect.center())
    return True


def main() -> int:
    if not acquire_lock():
        print("⛔ 拿不到 _check/.app_run.lock（另一个真实运行在跑），放弃")
        return 2
    app = None
    window = None
    dialog = None
    settings = None
    try:
        os.makedirs(CHECK_DIR, exist_ok=True)
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        from ui_theme import apply_scheme, ui_font
        app.setFont(ui_font(12.5))
        apply_scheme("dark")

        import ui_anim
        import customization
        # 与 main.py 完全同一条：anim_fps==120 是"跟随屏幕"的哨兵
        hz = int(customization.get("anim_fps"))
        if hz == 120:
            hz = ui_anim.display_max_hz()
        hz = ui_anim.set_frame_hz(hz)
        say(f"=== 环境 ===  屏幕 {app.primaryScreen().geometry()}  "
            f"dpr {app.primaryScreen().devicePixelRatio()}  "
            f"刷新率 {app.primaryScreen().refreshRate():.0f}Hz")
        say(f"  动画帧率 frame_hz() = {ui_anim.frame_hz()}  "
            f"帧间隔 = {ui_anim.frame_interval_ms()}ms  "
            f"display_max_hz() = {ui_anim.display_max_hz()}")

        from selftest import make_window, restore_settings
        settings = QSettings("MimoWorks", "SerialAssistant")
        settings.sync()
        snapshot = {key: settings.value(key) for key in settings.allKeys()}
        say(f"  QSettings 快照 {len(snapshot)} 个键")

        build_vehicles()
        window = make_window()
        window.setGeometry(60, 40, WIN_W, WIN_H)
        window.show()
        pump(app, 700)
        # 喂一段电压数据：接收区里"已经显示的文本"才会被 200ms 后的重排动到
        window._on_received(("Voltage1:\n" + " ".join(
            f"{i:02d}.3700" for i in range(1, 76)) + "\nTemperature1:\n" + " ".join(
            f"{i:02d}.25" for i in range(1, 20))).encode("utf-8"))
        window._flush_rx()
        pump(app, 600)
        say(f"  主窗 {window.width()}x{window.height()}  "
            f"接收区块数 {window.txt_rx.document().blockCount()}")

        # 走**真入口**开设置面板（含 260ms 放大动画）
        window._open_settings()
        dialog = window._settings_dialog
        pump(app, 800)
        if dialog is None:
            say("⛔ 设置面板没打开")
            return 3
        scroll = dialog.findChild(QScrollArea)
        say(f"  面板 {dialog.frameGeometry()}  body 高 "
            f"{dialog.form.parentWidget().height()}  滚动条 max="
            f"{scroll.verticalScrollBar().maximum()}")
        scroll_to_vehicle(dialog, app)
        say(f"  滚到车辆节后滚动条 value={scroll.verticalScrollBar().value()} "
            f"max={scroll.verticalScrollBar().maximum()}")

        def wait_idle(app, timeout_ms: float = 2500.0) -> float:
            """等所有补间跑完（每一步的真实起点都必须是"静止"）。"""
            import ui_anim
            begin = time.perf_counter()
            deadline = begin + timeout_ms / 1000.0
            while time.perf_counter() < deadline:
                app.processEvents()
                if not ui_anim.ticker_active():
                    break
                time.sleep(0.002)
            app.processEvents()
            return (time.perf_counter() - begin) * 1000.0

        def set_car(app, name: str | None) -> float:
            """直接切到某台车（不测，只为了把状态摆到场景起点）。"""
            index = combo_index(dialog, name or "")
            dialog.cmb_vehicle_number.setCurrentIndex(max(0, index))
            return wait_idle(app)

        def go(name: str):
            index = combo_index(dialog, name)
            if index < 0:
                raise SystemExit(f"找不到条目 {name!r}")
            return lambda: dialog.cmb_vehicle_number.setCurrentIndex(index)

        # (标题, 起点车, 目标车)
        scenarios = [
            ("A1 3模组→5模组", CAR_A, CAR_B),
            ("A2 5模组→3模组", CAR_B, CAR_A),
            ("A3 3模组 25/25/25→10/20/30（行数不变）", CAR_A, CAR_C),
            ("A4 未选择→3模组（旧三件套整行隐藏）", None, CAR_A),
        ]

        results: dict[str, dict] = {}
        with TickTap(app, dialog, window) as tap:
            for title, from_car, to_car in scenarios:
                idle_ms = set_car(app, from_car)
                say(f"\n=== {title} ===")
                say(f"  就位：当前车={customization.current_vehicle()!r} "
                    f"逐模组行={len(dialog._module_rows)} 等静止 {idle_ms:.0f}ms")
                rec = tap.run(go(to_car), settle_ms=1200)
                results[title] = rec
                say(f"  执行后：当前车={customization.current_vehicle()!r} "
                    f"逐模组行={len(dialog._module_rows)} "
                    f"旧三件套可见="
                    f"{[dialog._custom_rows[k][1].isVisibleTo(dialog) for k in dialog._LEGACY_GRID_KEYS]}")
                analyse(title, rec, ui_anim.frame_hz())

            # A5：真下拉弹层点击（用户的实际动作，含 popup 开/关）
            start = CAR_B if customization.current_vehicle() != CAR_B else CAR_A
            target = CAR_A if start == CAR_B else CAR_B
            set_car(app, start)
            index = combo_index(dialog, target)

            def popup_action() -> None:
                if not click_popup_item(dialog.cmb_vehicle_number, index):
                    dialog.cmb_vehicle_number.setCurrentIndex(index)

            rec = tap.run(popup_action, settle_ms=1200)
            results["A5 弹层点击"] = rec
            say("\n=== A5 弹层点击（真鼠标事件：showPopup → 点条目）===")
            say(f"  起点 {start} → 目标 {target}；执行后："
                f"当前车={customization.current_vehicle()!r} "
                f"逐模组行={len(dialog._module_rows)}")
            analyse("A5 弹层点击", rec, ui_anim.frame_hz())

        # ---- 像素通道（残影/双字/闪白）----
        if PIXELS:
            set_car(app, CAR_A)
            scroll_to_vehicle(dialog, app)
            with PixelTap(app, dialog) as px:
                frames = px.run(go(CAR_B), settle_ms=1000)
                say(f"\n=== 像素 A1（3→5）grab 耗时 中位 "
                    f"{statistics.median(px.costs) if px.costs else -1:.2f}ms "
                    f"最大 {max(px.costs) if px.costs else -1:.2f}ms "
                    f"错误 {px.errors[:2]} ===")
                pixel_report("A1 3→5", frames, "a1")
                wait_idle(app)
                frames = px.run(go(CAR_A), settle_ms=1000)
                say(f"\n=== 像素 A2（5→3）grab 耗时 中位 "
                    f"{statistics.median(px.costs) if px.costs else -1:.2f}ms "
                    f"最大 {max(px.costs) if px.costs else -1:.2f}ms "
                    f"错误 {px.errors[:2]} ===")
                pixel_report("A2 5→3", frames, "a2")

        return 0
    finally:
        for widget in (dialog, window):
            if widget is not None:
                try:
                    widget.close()
                except RuntimeError:
                    pass
        if app is not None:
            pump(app, 250)
        if settings is not None:
            from selftest import restore_settings
            restore_settings(settings, snapshot)
            settings.sync()
            settings = None
        # 关掉实例之后再开一个新的核对（确保真的落到注册表）
        check = QSettings("MimoWorks", "SerialAssistant")
        check.sync()
        say("\n=== 收尾核对（必须与跑之前一致）===")
        for key in ("geometry", "custom/startup_maximized", "custom/vehicle",
                    "custom/vehicles", "custom/anim_fps", "custom/module_count"):
            say(f"  {key} = {check.value(key)!r}")
        with open(OUT_TXT, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_lines))
        print(f"\n产物：{OUT_TXT}")
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
