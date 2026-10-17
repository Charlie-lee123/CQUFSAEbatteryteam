# -*- coding: utf-8 -*-
r"""QA 独立验收探针（本轮 A–G）：D 高负载帧率 / E 非模态设置面板 / F 功能列间距 / B 网格算法 / A 默认 0 兼容。

不复用工程师的 ``devtools/_probe_round2.py``，全部自己量。

用法：
    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools\_qa_round2_check.py
"""
import os
import subprocess
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QEvent, QObject, QSettings, QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton                 # noqa: E402

from ui_theme import apply_scheme, ui_font                              # noqa: E402

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    return ok


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    i = int(round(fraction * (len(sorted_values) - 1)))
    return sorted_values[max(0, min(len(sorted_values) - 1, i))]


# ------------------------------------------------------------- 逐帧记录器

class _TickRecorder(QObject):
    """挂在 ui_anim 的共享计时器上，**每引擎帧**记一次（避免轮询采样造成混叠）。"""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list = []
        self.watch = None
        self._on = False

    def attach(self) -> None:
        from ui_anim import _ticker
        _ticker()._timer.timeout.connect(self._on_tick)

    def _on_tick(self) -> None:
        if self._on and self.watch is not None:
            self.rows.append((time.perf_counter(), self.watch()))

    def start(self, watch) -> None:
        self.rows = []
        self.watch = watch
        self._on = True

    def finish(self):
        self._on = False
        return self.rows


class _PaintCounter(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def eventFilter(self, obj, event) -> bool:      # noqa: N802
        if event.type() == QEvent.Type.Paint:
            self.count += 1
        return False


# ----------------------------------------------------------------- D：高负载

class _Feeder:
    """持续高速喂数据（≥200 行/秒），走应用真实的收数入口 ``_on_received``。"""

    def __init__(self, window, lines_per_tick: int = 2, interval_ms: int = 5) -> None:
        self._window = window
        self._lines = lines_per_tick
        self._n = 0
        self.sent = 0
        self.timer = QTimer()
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        lines = []
        for _ in range(self._lines):
            self._n = (self._n % 9999) + 1
            lines.append(f"{self._n}.3700 {self._n}.3701 {self._n}.3699 {self._n}.3702")
        self.sent += len(lines)
        self._window._on_received(("\r\n".join(lines) + "\r\n").encode())

    def start(self) -> None:
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()


def _watch(window, paint):
    return (window.txt_rx.isVisible(), window.txt_rx.height(),
            window.txt_rx.updatesEnabled(), paint.count)


def test_d_under_load(app, window, recorder) -> None:
    """D：持续高速收数据的同时反复最大化/退出 —— 帧率 + 接收区不消失。

    三组**对照**缺一不可，否则说不清掉帧到底是谁造成的：
      · 对照组：只收数据、不做最大化（数据量本身的成本）
      · 现状组：收数据 + 最大化（`_suspend_editors` 已是空实现）
      · 冻结组：收数据 + 最大化 + 手动冻结编辑器（模拟**旧行为**）
    """
    paint = _PaintCounter()
    window.txt_rx.installEventFilter(paint)
    feeder = _Feeder(window, lines_per_tick=3, interval_ms=4)   # ≈250 行/秒
    feeder.start()
    pump(app, 0.5)                                  # 先让数据流起来

    def run(tag, action, seconds=1.2, freeze=False):
        if freeze:
            window.txt_rx.setUpdatesEnabled(False)
            window.txt_tx.setUpdatesEnabled(False)
        recorder.start(lambda: _watch(window, paint))
        if action is not None:
            action()
        pump(app, seconds)
        rows = recorder.finish()
        if freeze:
            window.txt_rx.setUpdatesEnabled(True)
            window.txt_tx.setUpdatesEnabled(True)
        return (tag, rows)

    stats = []
    controls = []
    stats.append(run("对照组·只收数据(无最大化)", None))
    for cycle in range(3):
        stats.append(run(f"现状·展开#{cycle}", window._expand_receive))
        pump(app, 0.5)
        stats.append(run(f"现状·退出#{cycle}", window._collapse_receive))
        pump(app, 0.5)
    pump(app, 0.4)
    # 冻结组 = 故意还原**旧行为**，只作对照（不参与 PASS/FAIL）
    controls.append(run("冻结组·展开(旧行为对照)", window._expand_receive, freeze=True))
    pump(app, 0.5)
    controls.append(run("冻结组·退出(旧行为对照)", window._collapse_receive, freeze=True))
    feeder.stop()
    window.txt_rx.removeEventFilter(paint)

    elapsed = 0.5 + 1.2 + 3 * (1.2 + 0.5 + 1.2 + 0.5) + 0.4 + 1.2 + 0.5 + 1.2
    check("D/收数速率达标（≥200 行/秒）", feeder.sent >= 200 * elapsed,
          f"共喂 {feeder.sent} 行 / {elapsed:.1f}s ≈ {feeder.sent / elapsed:.0f} 行/秒")

    for name, rows in stats:
        times = [r[0] for r in rows]
        intervals = [(b - a) * 1000.0 for a, b in zip(times, times[1:]) if b > a]
        fps = sorted(1000.0 / ms for ms in intervals) if intervals else []
        p95 = _percentile(fps, 0.95) if fps else 0.0
        p50 = 1000.0 / _percentile(sorted(intervals), 0.50) if intervals else 0.0
        over = sum(1 for ms in intervals if ms > 20.0)
        # 不可见帧：isVisible False 或 高度<=0
        invisible = sum(1 for _t, (vis, h, _u, _p) in rows if (not vis or h <= 0))
        # 冻结帧：updatesEnabled 被关掉的帧（这就是原来"接收区消失"的机制）
        frozen = sum(1 for _t, (_v, _h, u, _p) in rows if not u)
        # 最长连续"0 次 Paint"的帧数
        run = worst = 0
        for (_t1, (_v1, _h1, _u1, p1)), (_t2, (_v2, _h2, _u2, p2)) in zip(rows, rows[1:]):
            run = run + 1 if p2 == p1 else 0
            worst = max(worst, run)
        # "不消失"是硬指标（用户报的 bug 就是这个），必须 100% 满足
        check(f"D/{name} 接收区不消失（不可见帧=0 且 未冻结绘制）",
              invisible == 0 and frozen == 0,
              f"不可见帧={invisible} 冻结帧={frozen} 最长无绘制连续帧={worst}")
        ok = p95 >= 120.0 and over == 0
        check(f"D/{name} 帧率 P95≥120 且 >20ms=0", ok,
              f"帧数={len(rows)} P50={p50:.1f}fps P95={p95:.1f}fps "
              f"max={max(intervals):.2f}ms >20ms={over}")

    # 冻结组只报 INFO：它**故意**还原旧行为，用来证明检测器有效 + 量化代价比
    for name, rows in controls:
        times = [r[0] for r in rows]
        intervals = [(b - a) * 1000.0 for a, b in zip(times, times[1:]) if b > a]
        fps = sorted(1000.0 / ms for ms in intervals) if intervals else []
        p95 = _percentile(fps, 0.95) if fps else 0.0
        p50 = 1000.0 / _percentile(sorted(intervals), 0.50) if intervals else 0.0
        over = sum(1 for ms in intervals if ms > 20.0)
        frozen = sum(1 for _t, (_v, _h, u, _p) in rows if not u)
        print(f"INFO  {name}（旧行为对照，不判 PASS/FAIL）: "
              f"P50={p50:.1f}fps P95={p95:.1f}fps max={max(intervals):.2f}ms "
              f">20ms={over} 冻结帧={frozen}")
    if controls:
        check("D/检测器自检：冻结组确实被判成'冻结帧'（证明检测有效）",
              all(sum(1 for _t, (_v, _h, u, _p) in rows if not u) > 0
                  for _n, rows in controls),
              "旧行为下冻结帧>0，现状下=0 → 检测有效")


# ----------------------------------------------------------------- E：非模态

def _dialog_button(dialog, text: str):
    for w in dialog.findChildren(QPushButton):
        if w.text().strip() == text:
            return w
    return None


def _set_choice(dialog, key: str, value: str) -> bool:
    combo = getattr(dialog, "_custom_widgets", {}).get(key)
    if combo is None:
        return False
    idx = combo.findData(value)
    if idx < 0:
        return False
    combo.setCurrentIndex(idx)
    return True


def _open_and_settle(app, window, seconds: float = 0.55):
    window._open_settings()
    pump(app, seconds)
    return window._settings_dialog


def test_e_cancel(app, window) -> None:
    """E1：改值 → 取消 → 全部还原（含注册表）。"""
    from ui_theme import glass_strength, material
    settings = QSettings("MimoWorks", "SerialAssistant")
    before_glass = glass_strength()
    before_material = material()
    before_reg_glass = settings.value("glass")

    dialog = _open_and_settle(app, window)
    if dialog is None:
        check("E1/面板能打开", False, "_settings_dialog 为 None")
        return
    dialog.slider_glass.setValue(90)
    _set_choice(dialog, "material", "liquid")
    pump(app, 0.1)
    preview_glass = glass_strength()
    preview_material = material()
    confirm = _dialog_button(dialog, "取消")
    if confirm is None:
        check("E1/找到取消按钮", False)
        return
    confirm.click()
    # ⚠️ 关窗动画期间面板是否仍然可见（若 accept/reject 直接 hide 了，动画就看不见）
    visible_during_close = dialog.isVisible()
    pump(app, 0.7)
    check("E1/取消：强度还原", glass_strength() == before_glass,
          f"预览={preview_glass} 取消后={glass_strength()} 原值={before_glass}")
    check("E1/取消：材质还原", material() == before_material,
          f"预览={preview_material} 取消后={material()} 原值={before_material}")
    check("E1/取消：注册表未落盘", settings.value("glass") == before_reg_glass,
          f"registry glass={settings.value('glass')!r} 原值={before_reg_glass!r}")
    check("E1/关闭动画期间面板仍可见（未被 accept/reject 直接隐藏）",
          visible_during_close, f"点击后立刻 isVisible={visible_during_close}")


def test_e_accept(app, window) -> None:
    """E2：改值 → 确定 → 落盘 + 重启后保持。"""
    import customization
    from ui_theme import glass_strength, material, set_glass_strength, set_material
    settings = QSettings("MimoWorks", "SerialAssistant")
    dialog = _open_and_settle(app, window)
    if dialog is None:
        check("E2/面板能打开", False)
        return
    dialog.slider_glass.setValue(77)
    _set_choice(dialog, "material", "liquid")
    pump(app, 0.1)
    confirm = _dialog_button(dialog, "确定")
    if confirm is None:
        check("E2/找到确定按钮", False)
        return
    confirm.click()
    visible_during_close = dialog.isVisible()
    pump(app, 0.7)
    check("E2/确定：强度生效", glass_strength() == 77, f"glass={glass_strength()}")
    check("E2/确定：材质生效", material() == "liquid", f"material={material()!r}")
    check("E2/确定：强度落盘", int(settings.value("glass")) == 77,
          f"registry glass={settings.value('glass')!r}")
    check("E2/确定：材质落盘", str(customization.get("material")) == "liquid",
          f"customization.material={customization.get('material')!r}")
    check("E2/关闭动画期间面板仍可见", visible_during_close,
          f"isVisible={visible_during_close}")

    # 重启后保持
    from main_window import MainWindow
    set_glass_strength(0)
    set_material("frosted")
    w2 = MainWindow()
    w2._port_watch.stop()
    w2.setGeometry(0, 0, 1134, 820)
    pump(app, 0.3)
    check("E2/重启后：强度恢复 77", glass_strength() == 77, f"glass={glass_strength()}")
    check("E2/重启后：材质恢复 liquid", material() == "liquid", f"material={material()!r}")
    w2.setGeometry(0, 0, 1134, 820)
    w2.close()
    w2.deleteLater()
    pump(app, 0.2)
    # 复原，别影响后续用例
    set_glass_strength(0)
    set_material("frosted")


def test_e_double_click(app, window) -> None:
    """E3：动画中途再点设置键 —— 不得出现两个面板 / 不得卡死。"""
    window._open_settings()
    pump(app, 0.05)                        # 入场动画进行中
    window._open_settings()                # 中途再点一次
    pump(app, 0.1)
    panels = [w for w in QApplication.topLevelWidgets()
              if type(w).__name__ == "SettingsDialog" and w.isVisible()]
    check("E3/中途再点不会叠出第二个面板", len(panels) <= 1,
          f"可见面板数={len(panels)}")
    pump(app, 0.7)
    check("E3/中途再点后面板收掉（未被卡死）", window._settings_dialog is None,
          f"_settings_dialog={window._settings_dialog!r}")
    # 再点一次应当能重新打开
    window._open_settings()
    pump(app, 0.6)
    reopened = window._settings_dialog is not None
    check("E3/收掉之后还能重新打开", reopened)
    if reopened:
        window._close_settings_dialog(accepted=False)
        pump(app, 0.7)


def test_e_close_click(app, window) -> None:
    """E4：点了「确定」后、关闭动画进行中再点设置键 —— 不得崩。"""
    from ui_theme import glass_strength, material
    before_glass = glass_strength()
    before_material = material()
    settings = QSettings("MimoWorks", "SerialAssistant")
    before_reg = settings.value("glass")
    dialog = _open_and_settle(app, window)
    if dialog is None:
        check("E4/面板能打开", False)
        return
    dialog.slider_glass.setValue(55)
    confirm = _dialog_button(dialog, "确定")
    confirm.click()
    pump(app, 0.05)                        # 关闭动画进行中
    crashed = False
    try:
        window.btn_settings.click()        # 再点一次设置键
        pump(app, 0.8)
    except Exception as exc:               # noqa: BLE001
        crashed = True
        check("E4/关闭动画中再点不崩", False, f"{type(exc).__name__}: {exc}")
    if not crashed:
        check("E4/关闭动画中再点不崩", True,
              f"glass 现={glass_strength()} 原={before_glass}；"
              f"registry glass={settings.value('glass')!r} 原={before_reg!r}")
        # 语义观察：确定之后又点设置键，结果是否被降级成取消
        downgraded = (glass_strength() != 55)
        print(f"      ↳ 观察：确定(55) 后关闭动画中再点设置键 → "
              f"最终 glass={glass_strength()}（{'被降级为取消' if downgraded else '确定生效'}）")
    # 复原
    settings.setValue("glass", before_reg)
    settings.sync()
    window._apply_glass(int(before_reg))
    window._apply_material(before_material)


def test_e_anchor(app, window, recorder) -> None:
    """E5：锚点真实性 —— 首帧矩形 == 设置按钮屏幕矩形；收场末帧 == 同一位置。"""
    btn = window._settings_button_rect()
    # 入场
    window._open_settings()
    dialog = window._settings_dialog
    first = dialog.geometry() if dialog is not None else None
    recorder.start(lambda: dialog.geometry() if dialog is not None else None)
    pump(app, 0.7)
    geoms = [g for _t, g in recorder.finish()]
    last_open = geoms[-1] if geoms else None
    end_center = last_open.center() if last_open is not None else None
    win_center = window.frameGeometry().center()

    def pos_of(rect):
        return (rect.x(), rect.y()) if rect is not None else None

    def size_of(rect):
        return (rect.width(), rect.height()) if rect is not None else None

    pos_ok = first is not None and pos_of(first) == pos_of(btn)
    size_ok = first is not None and size_of(first) == size_of(btn)
    check("E5/入场首帧**位置** == 设置按钮屏幕位置", pos_ok,
          f"首帧位置={pos_of(first)} 按钮位置={pos_of(btn)}")
    check("E5/入场首帧**尺寸** == 设置按钮尺寸（未被最小尺寸夹住）", size_ok,
          f"首帧尺寸={size_of(first)} 按钮尺寸={size_of(btn)} "
          f"面板 minimumSize={dialog.minimumSize() if dialog else None}")

    center_off = (abs(end_center.x() - win_center.x()),
                  abs(end_center.y() - win_center.y())) if end_center else (999, 999)
    check("E5/入场末帧面板居中于父窗（允许 ≤40px 的框架偏移）",
          max(center_off) <= 40, f"中心偏移 dx={center_off[0]} dy={center_off[1]}")

    # 收场
    recorder.start(lambda: dialog.geometry())
    window._close_settings_dialog(accepted=False)
    pump(app, 0.9)
    geoms2 = [g for _t, g in recorder.finish()]
    last_close = geoms2[-1] if geoms2 else None
    check("E5/收场末帧**位置** == 同一按钮位置",
          last_close is not None and pos_of(last_close) == pos_of(btn),
          f"末帧位置={pos_of(last_close)} 按钮位置={pos_of(btn)}")
    check("E5/收场末帧**尺寸** == 按钮尺寸", size_ok and
          (last_close is not None and size_of(last_close) == size_of(btn)),
          f"末帧尺寸={size_of(last_close)} 按钮尺寸={size_of(btn)}")
    # 收场是否真的**连续**回到按钮（而不是一帧跳回去）
    steps = [abs(g.x() - h.x()) + abs(g.y() - h.y())
             for g, h in zip(geoms2, geoms2[1:])]
    span = (abs(geoms2[0].x() - btn.x()) + abs(geoms2[0].y() - btn.y())
            if geoms2 else 0)
    check("E5/收场是连续位移（单帧位移 ≤ 半程）",
          bool(steps) and max(steps) <= max(8, span * 0.5),
          f"单帧最大位移={max(steps) if steps else 0}px 总位移={span}px 帧数={len(geoms2)}")


# ----------------------------------------------------------------- F：间距

def test_f_rail_gap(app, window) -> None:
    """F：最大化态下功能列是否真的**贴着**数据卡片。

    ⚠️ 实测确认 ``_rx_rail`` 是**接收卡片的子控件**（不是窗口右侧独立一列），
    所以"贴着卡片"要看两个方向：
      · 水平：列右缘 → 卡片外缘（≤16px）
      · 垂直：列顶 → 卡片顶（用户点名的是"按钮离那么远"＝**垂直**方向）
    """
    from PySide6.QtCore import QPoint

    def metrics():
        card, rail = window._rx_card, window._rx_rail
        card_top_left = card.mapTo(window, QPoint(0, 0))
        card_right = card.mapTo(window, QPoint(card.width(), 0)).x()
        rail_top_left = rail.mapTo(window, QPoint(0, 0))
        rail_right = rail.mapTo(window, QPoint(rail.width(), 0)).x()
        return {
            "gap_x": int(card_right - rail_right),
            "gap_top": int(rail_top_left.y() - card_top_left.y()),
            "card_h": card.height(),
            "rail_h": rail.height(),
        }

    if window._expanded:
        window._collapse_receive()
        pump(app, 1.0)
    normal = metrics()
    window._expand_receive()
    pump(app, 1.4)
    exp = metrics()

    check("F/水平：功能列右缘 → 卡片外缘 ≤16px", exp["gap_x"] <= 16,
          f"最大化={exp['gap_x']}px（普通态={normal['gap_x']}px；"
          f"卡片自带 SHADOW_PAD+CARD_PAD=24px，故该值=卡片内边距）")
    # 垂直：贴住卡片 ⇒ 列顶应靠近卡片顶（远小于"居中"时的 (卡高-列高)/2）
    centered = (exp["card_h"] - exp["rail_h"]) / 2
    check("F/垂直：最大化态功能列贴住卡片顶部（不再垂直居中）",
          exp["gap_top"] <= 60,
          f"列顶距卡顶={exp['gap_top']}px；若仍居中应≈{centered:.0f}px "
          f"（卡高={exp['card_h']} 列高={exp['rail_h']}）")
    check("F/垂直：最大化态列高=内容高（未被拉满整列）",
          exp["rail_h"] < exp["card_h"] * 0.5,
          f"列高={exp['rail_h']} 卡高={exp['card_h']}")
    window._collapse_receive()
    pump(app, 1.0)
    back = metrics()
    check("F/退出最大化后还原", abs(back["gap_x"] - normal["gap_x"]) <= 2
          and abs(back["gap_top"] - normal["gap_top"]) <= 4,
          f"还原后 x={back['gap_x']} top={back['gap_top']}；"
          f"原 x={normal['gap_x']} top={normal['gap_top']}")


# ----------------------------------------------------------------- B：算法

def test_b_grid() -> None:
    """B：grid_row_sizes 全覆盖 N=1..20 × 不同行/列提示。"""
    from ui_highlight import grid_row_sizes

    bad_sum, bad_range, bad_last, empty = [], [], [], []
    for n in range(1, 21):
        for rows_hint in (0, 1, 2, 3, 4, 5, 6, 7, 8, 13):
            for cols_hint in (0, 1, 2, 3, 4, 6, 8, 16):
                sizes = grid_row_sizes(n, rows_hint, cols_hint)
                if not sizes:
                    empty.append((n, rows_hint, cols_hint))
                    continue
                if sum(sizes) != n:
                    bad_sum.append((n, rows_hint, cols_hint, sizes))
                if max(sizes) - min(sizes) > 1:
                    bad_range.append((n, rows_hint, cols_hint, sizes))
                if len(sizes) > 1 and sizes[-1] < sizes[0]:
                    bad_last.append((n, rows_hint, cols_hint, sizes))
    check("B/各行之和 == N（不丢点）", not bad_sum, f"反例 {bad_sum[:3]}")
    check("B/极差 ≤1", not bad_range, f"反例 {bad_range[:3]}")
    check("B/末行 ≥ 首行（末排只多不少）", not bad_last, f"反例 {bad_last[:3]}")
    check("B/正常输入不返回空", not empty, f"空返回 {empty[:3]}")

    # 工程师声称的三例
    claims = [((7, 3, 0), [2, 2, 3]), ((8, 3, 0), [2, 3, 3]), ((13, 5, 0), [2, 2, 3, 3, 3])]
    for (n, r, c), want in claims:
        got = grid_row_sizes(n, r, c)
        check(f"B/声称 N={n} rows={r} → {want}", got == want, f"实测 {got}")

    # 装不下时以数据量为准（不丢点）
    tight = grid_row_sizes(20, 3, 2)       # 3×2=6 装不下 20
    check("B/rows×cols 装不下时以数据量为准", sum(tight) == 20 and len(tight) >= 10,
          f"grid_row_sizes(20,3,2)={tight} 和={sum(tight)}")


# ----------------------------------------------------------------- A：默认

def test_a_defaults(app, window) -> None:
    """A：三个新配置默认 0；默认 0 时排版与老行为（git HEAD）一致。"""
    import customization
    defaults = {k: customization.get(k) for k in
                ("module_count", "cells_per_module", "temps_per_module")}
    check("A/三个新配置默认值为 0", set(defaults.values()) == {0}, f"{defaults}")
    hints = window._rx_grid_hints()
    check("A/默认时 _rx_grid_hints 全 0", set(hints.values()) == {0}, f"{hints}")

    # 与 git HEAD 的老实现逐样本对比（只读 git，不改工作区）
    try:
        old_src = subprocess.run(
            ["git", "show", "HEAD:ui_highlight.py"], cwd=_ROOT,
            capture_output=True, check=True).stdout.decode("utf-8")
        import tempfile
        tmp = os.path.join(tempfile.gettempdir(), "_qa_old_highlight.py")
        open(tmp, "w", encoding="utf-8").write(old_src)
        import importlib.util
        spec = importlib.util.spec_from_file_location("_qa_old_highlight", tmp)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
    except Exception as exc:                     # noqa: BLE001
        check("A/取到 git HEAD 的旧 ui_highlight 作对照", False,
              f"{type(exc).__name__}: {exc}")
        return

    from ui_highlight import align_indexes
    samples = [
        "Voltage1:3.700 Voltage2:3.701 Voltage3:3.699\n"
        "4.3700 5.3701 6.3699\n"
        "7.3700 8.3701 9.3699 10.3702",
        "Temperature1:1.22 2.23 3.24\n4.25 5.26",
        "1.3700 2.3701\n3.3699",
    ]
    for i, sample in enumerate(samples):
        # 老实现没有 rows_hint/volt_cols_hint/temp_cols_hint 三个参数；
        # 新实现**不传**它们（默认 0）就应当与老实现逐字一致。
        old_out = old.align_indexes(sample, max_row_chars=0)
        new_out = align_indexes(sample, max_row_chars=0)
        check(f"A/样本{i + 1} 默认 0 与老行为逐字一致", old_out == new_out,
              "一致" if old_out == new_out else f"老={old_out!r} 新={new_out!r}")

    # 给了正数时才改变布局（证明开关真的有效）
    legacy = old.align_indexes(samples[0], max_row_chars=0)
    changed = align_indexes(samples[0], max_row_chars=0, rows_hint=3)
    first_line = changed.splitlines()[0] if changed else ""
    check("A/给了模组个数(rows_hint=3)后布局确实变化", changed != legacy,
          f"新布局首行={first_line!r}")

    # 量化"默认 0 与老行为不一致"的影响面：N 个点 × 设备每行 cols 个，逐格对比
    diffs = []
    for cols in range(1, 9):
        for n in range(1, 25):
            # 模拟"设备自己按 cols 个一行发"（带 Voltage 头，进入电压段网格重排）
            tokens = [f"{i}.3700" for i in range(1, n + 1)]
            text = ("Voltage1:3.700 Voltage2:3.701\n"
                    + "\n".join(" ".join(tokens[i:i + cols])
                                for i in range(0, n, cols)))
            o = old.align_indexes(text, max_row_chars=0)
            v = align_indexes(text, max_row_chars=0)
            if o != v:
                diffs.append((cols, n))
    total_cases = 8 * 24
    print(f"      ↳ 影响面：{len(diffs)}/{total_cases} 组（设备列数 1..8 × 点数 1..24）"
          f"在**默认 0** 下与老实现输出不同；样例 {diffs[:6]}")
    check("A/默认 0 与老行为完全一致（覆盖 192 组）", not diffs,
          f"不一致 {len(diffs)} 组，样例 {diffs[:6]}")


# ----------------------------------------------------------------- main

def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_anim import set_animations_enabled_override
    set_animations_enabled_override(True)

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    import customization
    for spec in customization.specs():
        customization.set(spec["key"], spec["default"])

    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    from main_window import MainWindow
    window = MainWindow()
    window._port_watch.stop()
    window.setGeometry(0, 0, 1180, 860)
    window.show()
    pump(app, 1.0)

    recorder = _TickRecorder()
    recorder.attach()

    try:
        test_b_grid()                       # 纯算法，先跑
        test_a_defaults(app, window)
        test_d_under_load(app, window, recorder)
        test_e_cancel(app, window)
        test_e_accept(app, window)
        test_e_double_click(app, window)
        test_e_close_click(app, window)
        test_e_anchor(app, window, recorder)
        test_f_rail_gap(app, window)
    finally:
        window.setGeometry(0, 0, 1134, 820)
        window.close()
        for key in set(settings.allKeys()) - set(snapshot):
            settings.remove(key)
        for key, value in snapshot.items():
            if value is not None:
                settings.setValue(key, value)
        settings.sync()

    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    print("\n" + "=" * 74)
    print(f"本轮(A–G)验收：{passed}/{total} 通过")
    for name, ok, detail in _RESULTS:
        if not ok:
            print(f"  FAIL: {name}  [{detail}]")
    print("=" * 74)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
