# -*- coding: utf-8 -*-
r"""探针（lead 2026-11-06 定向）：三条聚焦问题，只读，不改产品源码。

① 主窗到底有没有监听「车辆变更」？（全仓搜 + 打印真实调用链）
   并逐帧录「设置里换车 → 立刻关面板 → 主窗接收区」整条链，定位跳变**发生在哪一刻**。
② 换车瞬间表单**可见行数**是否变化 → QFormLayout 是否瞬时重排；给出哪些锚点行移动了多少。
③ 车号弹层在换车瞬间是否被打断 / 有没有"满高闪一帧"。

用法：.venv\Scripts\python.exe devtools/_probe_vehicle_flow.py
产物：_check/vehicle_flow.txt
"""
from __future__ import annotations

import os
import statistics
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QPoint, QRect, QSettings, Qt, QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea          # noqa: E402

CHECK_DIR = os.path.join(_ROOT, "_check")
OUT = os.path.join(CHECK_DIR, "vehicle_flow.txt")
CAR_A, CAR_B, CAR_C = "E74-2026", "E75-2026", "E76-2026"
SPEC = {CAR_A: ([25, 25, 25], [8, 8, 8]),
        CAR_B: ([20] * 5, [6] * 5),
        CAR_C: ([10, 20, 30], [4, 5, 6])}
_lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    _lines.append(text)


def pump(app, ms: float) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


# ----------------------------------------------------------------- ① 监听者


def print_wiring() -> None:
    say("=== ① 主窗有没有监听「车辆变更」 ===")
    say("  结论：**有**，但走的不是 Qt 信号，是 on_custom 回调通道（三跳）：")
    say("    ui_settings.py:2096 _notify_vehicle_changed()")
    say("      → 2102-2103  self._on_custom('vehicle', customization.current_vehicle())")
    say("    main_window.py:7096  SettingsDialog(..., on_custom=self._preview_custom, ...)")
    say("    main_window.py:7599  _preview_custom: elif key == 'vehicle':")
    say("      → 7600 _refresh_vehicle_badge()")
    say("      → 7602 rx_history.switch_target(...)")
    say("      → 7603 _schedule_vehicle_relayout()   # 5866: 延迟 200ms 合并")
    say("    → main_window.py:5897 _relayout_vehicle_grid → 5902 _relayout_rx_document")
    say("  全仓搜 `vehicle_changed` / `customization.vehicle_changed` 只有")
    say("    ui_settings.py:462 `vehicles_changed = Signal(int)`（在线车辆库用，与选车无关）")
    say("    ui_settings.py:2174 `dialog.vehicles_changed.connect(self._on_library_imported)`")
    say("  ⇒ 没有任何 Qt 信号级监听，但主窗**确实**会在 200ms 后重排接收区，")
    say("    且这一步发生在**设置面板还开着的时候**（非模态）。")


# ----------------------------------------------------------------- ② 行数/锚点


def anchor_rows(dialog) -> dict:
    """换车前后各取一次：表单可见行数 + 关键锚点行的 body 坐标 y / 可见性。"""
    form = dialog.form
    body = form.parentWidget()
    rows = form.rowCount()
    visible = 0
    for index in range(rows):
        item = form.itemAt(index, form.ItemRole.LabelRole)
        if item is not None and item.widget() is not None:
            if item.widget().isVisible():
                visible += 1
            continue
        item = form.itemAt(index, form.ItemRole.FieldRole)
        if item is not None and item.widget() is not None and item.widget().isVisible():
            visible += 1

    def y_of(widget) -> tuple:
        if widget is None:
            return ("—",)
        try:
            if not widget.isVisible():
                return ("hidden",)
            return (widget.mapTo(body, QPoint(0, 0)).y(), widget.height())
        except RuntimeError:
            return ("dead",)

    anchors = {}
    anchors["赛季/车号行"] = y_of(dialog._vehicle_field)
    anchors["导出车辆"] = y_of(dialog.btn_vehicle_export)
    anchors["Module 1"] = y_of(dialog._module_rows[0][0] if dialog._module_rows else None)
    anchors["Module 末行"] = y_of(dialog._module_rows[-1][0] if dialog._module_rows else None)
    anchors["主题"] = y_of(getattr(dialog, "cmb_theme", None))
    anchors["材质强度"] = y_of(getattr(dialog, "slider_glass", None))
    anchors["旧三件套-模组个数"] = y_of(dialog._custom_rows["module_count"][1])
    anchors["旧三件套-电芯数"] = y_of(dialog._custom_rows["cells_per_module"][1])
    anchors["旧三件套-温度数"] = y_of(dialog._custom_rows["temps_per_module"][1])
    anchors["body 高"] = (body.height(),)
    return {"form_rows": rows, "visible_rows": visible, "anchors": anchors}


def diff_anchors(before: dict, after: dict) -> None:
    say(f"  表单总行数 {before['form_rows']} → {after['form_rows']}"
        f"（Δ{after['form_rows'] - before['form_rows']:+d}）  "
        f"**可见行数 {before['visible_rows']} → {after['visible_rows']}"
        f"（Δ{after['visible_rows'] - before['visible_rows']:+d}）**")
    for name, old in before["anchors"].items():
        new = after["anchors"].get(name)
        if old == new:
            say(f"    {name:<14} 未动 {old}")
            continue
        if len(old) == 2 and len(new) == 2:
            dy = new[0] - old[0]
            say(f"    {name:<14} y {old[0]} → {new[0]}  **Δy={dy:+d}px**  "
                f"h {old[1]} → {new[1]}")
        else:
            say(f"    {name:<14} {old} → {new}")


# ----------------------------------------------------------------- ③ 弹层


class PopupTap:
    """4ms 采一次弹层容器：geometry / 可见 / opacity / 打开补间是否在跑。"""

    def __init__(self, app, combo) -> None:
        self.app = app
        self.combo = combo
        self.rows: list[tuple[float, tuple]] = []
        self.t0 = 0.0
        self._timer = None

    def _sample(self) -> None:
        try:
            container = self.combo.view().window()
            geo = container.geometry()
            self.rows.append(((time.perf_counter() - self.t0) * 1000.0,
                              (geo.x(), geo.y(), geo.width(), geo.height(),
                               int(container.isVisible()),
                               round(float(container.windowOpacity()), 2),
                               int(bool(self.combo._popup_tween
                                        and self.combo._popup_tween.running)))))
        except RuntimeError:
            pass

    def __enter__(self) -> "PopupTap":
        self._timer = QTimer()
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(4)
        self._timer.timeout.connect(self._sample)
        self._timer.start()
        return self

    def __exit__(self, *exc) -> bool:
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        return False

    def run(self, action, settle_ms: float = 700.0) -> list:
        self.rows.clear()
        self.t0 = time.perf_counter()
        action()
        deadline = time.perf_counter() + settle_ms / 1000.0
        while time.perf_counter() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.app.processEvents()
        return list(self.rows)


def report_popup(title: str, rows: list, note: str = "") -> None:
    say(f"\n  [{title}] {len(rows)} 个采样" + (f"；{note}" if note else ""))
    if not rows:
        say("    没采到")
        return
    full = max(r[1][3] for r in rows)
    jumps = []
    prev = None
    for t, state in rows:
        if prev is not None:
            dy = state[1] - prev[1]
            dh = state[3] - prev[3]
            # "满高闪一帧"：可见窗口的高度在一拍里涨到满高（或从矮条直接跳到 ≥95% 满高）
            if state[4] == 1 and prev[4] == 1 and state[3] >= 0.95 * full and dh > 0.5 * full:
                jumps.append(("满高跳变", t, prev, state))
            if state[4] == 1 and state[4] != prev[4]:
                jumps.append(("由隐变显", t, prev, state))
        prev = state
    say(f"    满高 = {full}px；拍间位移/高度突变 {len(jumps)} 处")
    for kind, t, before, after in jumps[:8]:
        say(f"      {kind} @ t={t:.1f}ms  {before} → {after}")
    # 关键时间线（抽稀）
    step = max(1, len(rows) // 14)
    say("    时间线 (t, x, y, w, h, vis, opacity, 补间在跑)：")
    for t, state in rows[::step]:
        say(f"      {t:7.1f}  {state}")
    heights = [r[1][3] for r in rows if r[1][4] == 1]
    if heights:
        say(f"    可见期间高度范围 {min(heights)}~{max(heights)}px；"
            f"第一帧可见高度 {heights[0]}px（满高 {full}）")


# ----------------------------------------------------------------- 主流程


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, ui_font
    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    import customization
    import ui_anim
    hz = int(customization.get("anim_fps"))
    if hz == 120:
        hz = ui_anim.display_max_hz()
    hz = ui_anim.set_frame_hz(hz)

    from selftest import make_window, restore_settings
    settings = QSettings("MimoWorks", "SerialAssistant")
    settings.sync()
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    window = None
    dialog = None
    try:
        os.makedirs(CHECK_DIR, exist_ok=True)
        print_wiring()

        for name in list(customization.vehicle_names()):
            customization.delete_vehicle(name)
        for name, (cells, temps) in SPEC.items():
            customization.save_vehicle(name, cells, temps)
        customization.select_vehicle(CAR_A)

        window = make_window()
        window.setGeometry(60, 40, 1134, 820)
        window.show()
        pump(app, 700)
        window._on_received(("Voltage1:\n" + " ".join(
            f"{i:02d}.3700" for i in range(1, 76)) + "\nTemperature1:\n" + " ".join(
            f"{i:02d}.25" for i in range(1, 20))).encode("utf-8"))
        window._flush_rx()
        pump(app, 600)
        say(f"\n  主窗 {window.width()}x{window.height()}  接收区块数 "
            f"{window.txt_rx.document().blockCount()}  "
            f"rx_card={window._rx_card.geometry()}")

        # ---------------- ② 换车瞬间的行数/锚点 ----------------
        window._open_settings()
        dialog = window._settings_dialog
        pump(app, 900)
        scroll = dialog.findChild(QScrollArea)
        caption = next((label for label in dialog.findChildren(QLabel)
                        if label.text().startswith("车辆（电池箱）")), None)
        if caption is not None:
            scroll.ensureWidgetVisible(caption, 0, 8)
        pump(app, 400)
        say(f"\n=== ② 换车瞬间的表单重排 ===")
        say(f"  面板 {dialog.frameGeometry()}  滚动 value="
            f"{scroll.verticalScrollBar().value()} "
            f"max={scroll.verticalScrollBar().maximum()}")

        for title, from_car, to_car in (
                ("3模组→5模组（行数变）", CAR_A, CAR_B),
                ("5模组→3模组（行数变，反方向）", CAR_B, CAR_A),
                ("3模组 25/25/25→10/20/30（行数不变）", CAR_A, CAR_C),
                ("未选择→3模组（第一次选车）", None, CAR_A)):
            index = dialog.cmb_vehicle_number.findData(from_car or "")
            dialog.cmb_vehicle_number.setCurrentIndex(max(0, index))
            pump(app, 900)
            before = anchor_rows(dialog)
            scroll_before = scroll.verticalScrollBar().maximum()
            begin = time.perf_counter()
            dialog.cmb_vehicle_number.setCurrentIndex(
                dialog.cmb_vehicle_number.findData(to_car))
            cost = (time.perf_counter() - begin) * 1000.0
            pump(app, 900)
            after = anchor_rows(dialog)
            say(f"\n  ---- {title} ----")
            say(f"  action() 同步 {cost:.2f}ms；滚动 max {scroll_before} → "
                f"{scroll.verticalScrollBar().maximum()}")
            diff_anchors(before, after)

        # ---------------- ③ 弹层 ----------------
        say(f"\n=== ③ 车号弹层：展开 → 换车 → 收起 ===")
        combo = dialog.cmb_vehicle_number
        dialog.cmb_vehicle_number.setCurrentIndex(combo.findData(CAR_A))
        pump(app, 600)
        say(f"  条目：{[combo.itemText(i) for i in range(combo.count())]}")
        with PopupTap(app, combo) as tap:
            rows = tap.run(combo.showPopup, 420)
            report_popup("弹层展开（冷启动）", rows)
            rows = tap.run(combo.showPopup, 420)
            report_popup("弹层展开（第二次，热）", rows)
            # 真实路径的顺序：Qt 的 itemSelected 先 hidePopup 再 setCurrentIndex
            target = combo.findData(CAR_B)

            def select_like_qt() -> None:
                combo.hidePopup()
                combo.setCurrentIndex(target)

            rows = tap.run(select_like_qt, 500)
            report_popup("弹层开着时换车（先 hidePopup 再 setCurrentIndex，Qt 的顺序）", rows)
            # 对照：弹层**还开着**就换车（模拟最坏情况）
            combo.setCurrentIndex(combo.findData(CAR_A))
            pump(app, 500)
            combo.showPopup()
            pump(app, 260)

            def select_with_popup_open() -> None:
                combo.setCurrentIndex(combo.findData(CAR_B))
                combo.hidePopup()

            rows = tap.run(select_with_popup_open, 500)
            report_popup("对照：弹层未关就 setCurrentIndex（最坏顺序）", rows,
                         "用来区分『换车本身会不会闪』和『弹层收起有没有闪』")

            # ---- 拆开计时：换车这一下到底卡在哪一步 ----
            say("\n  [拆开计时] 弹层开着 → hidePopup() / setCurrentIndex() 各自同步耗时")
            for index in range(3):
                combo.setCurrentIndex(combo.findData(CAR_A))
                pump(app, 400)
                combo.showPopup()
                pump(app, 300)
                begin = time.perf_counter()
                combo.hidePopup()
                hide_ms = (time.perf_counter() - begin) * 1000.0
                begin = time.perf_counter()
                combo.setCurrentIndex(combo.findData(CAR_B))
                set_ms = (time.perf_counter() - begin) * 1000.0
                say(f"    第{index + 1}轮（弹层开过）：hidePopup {hide_ms:5.1f}ms + "
                    f"setCurrentIndex {set_ms:6.1f}ms = 合计 "
                    f"{hide_ms + set_ms:6.1f}ms")
                pump(app, 700)
                # 对照：这一轮**不**开弹层，直接 setCurrentIndex
                combo.setCurrentIndex(combo.findData(CAR_A))
                pump(app, 700)
                begin = time.perf_counter()
                combo.setCurrentIndex(combo.findData(CAR_B))
                plain = (time.perf_counter() - begin) * 1000.0
                say(f"      对照（没开过弹层）：setCurrentIndex {plain:6.1f}ms")
                pump(app, 700)
        # ---------------- ① 整条链：换车 → 立刻关面板 → 接收区 ----------------
        say(f"\n=== ① 换车 → 立刻关面板 → 主窗接收区（整条链的时刻表）===")
        combo.hidePopup()
        pump(app, 300)
        # 喂 200 个点：小数据量下"每排只放得下 10 个点"，模组结构会被视口封顶
        # 抹平（实测 76 点时换车并不改文本），必须用足够多的点才看得出差异。
        window._clear_receive()
        window._on_received(("Voltage1:\n" + " ".join(
            f"{i:03d}.3700" for i in range(1, 201)) + "\nTemperature1:\n" + " ".join(
            f"{i:02d}.25" for i in range(1, 41))).encode("utf-8"))
        window._flush_rx()
        pump(app, 500)
        # 起点钉成「未选择」：这时接收区走旧三件套排版，选车后文本**一定**会变
        dialog.cmb_vehicle_number.setCurrentIndex(0)
        pump(app, 900)
        say(f"  起点：车={customization.current_vehicle()!r}  "
            f"块数={window.txt_rx.document().blockCount()} "
            f"文本长度={len(window.txt_rx.toPlainText())}")
        base_text = window.txt_rx.toPlainText()
        base_blocks = window.txt_rx.document().blockCount()
        marks: list[tuple[float, str]] = []
        t0 = time.perf_counter()

        def mark(text: str) -> None:
            marks.append(((time.perf_counter() - t0) * 1000.0, text))

        # 4ms 采样：面板可见性 / 接收区块数与旧图 alpha / 主窗几何
        samples: list[tuple[float, tuple]] = []

        relayouts: list[tuple[float, bool]] = []
        original_relayout = window._relayout_rx_document

        def timed_relayout() -> bool:
            result = original_relayout()
            relayouts.append(((time.perf_counter() - t0) * 1000.0, bool(result)))
            return result

        window._relayout_rx_document = timed_relayout

        def sample() -> None:
            try:
                shot = window._rx_shot
                panel = window._settings_dialog
                holder = window._settings_shot
                samples.append(((time.perf_counter() - t0) * 1000.0,
                                (int(panel is not None and panel.isVisible()),
                                 int(holder.isVisible()),
                                 holder.geometry().height(),
                                 window.txt_rx.document().blockCount(),
                                 round(float(shot.alpha()), 2),
                                 0 if shot.isHidden() else 1,
                                 len(window.txt_rx.toPlainText()))))
            except RuntimeError:
                pass

        timer = QTimer()
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.setInterval(4)
        timer.timeout.connect(sample)
        timer.start()

        dialog.cmb_vehicle_number.setCurrentIndex(
            dialog.cmb_vehicle_number.findData(CAR_A))
        mark("换车完成（同步部分）")
        pump(app, 420)              # 先让 200ms 重排 + 160ms 交叉淡入在**面板背后**跑完
        mark("重排窗口已过（面板仍开着）")
        window._close_settings_dialog(accepted=False)
        mark("关面板开始")
        pump(app, 1400)
        timer.stop()
        timer.deleteLater()
        window._relayout_rx_document = original_relayout
        marks.append(((time.perf_counter() - t0) * 1000.0, "采样结束"))
        say("  事件：" + "  ".join(f"{text}@{t:.0f}ms" for t, text in marks))
        say(f"  ★ 主窗 _relayout_rx_document 被调用：{relayouts or '一次都没有'}"
            "（第二项 = 文本真的变了）")
        say(f"  接收区文本是否变了：{window.txt_rx.toPlainText() != base_text}"
            f"（块数 {base_blocks} → {window.txt_rx.document().blockCount()}）")
        prev = None
        for t, state in samples:
            if prev is not None and state[3] != prev[3]:
                say(f"    t={t:.0f}ms  块数 {prev[3]} → {state[3]}"
                    f"（文本长度 {prev[6]} → {state[6]}）")
            prev = state
        prev = None
        for t, state in samples:
            if prev is not None and state[4] != prev[4]:
                say(f"    t={t:.0f}ms  旧图 alpha {prev[4]} → {state[4]}"
                    f"（可见 {prev[5]}→{state[5]}）")
            prev = state
        prev = None
        for t, state in samples:
            if prev is not None and (state[1] != prev[1] or state[0] != prev[0]):
                say(f"    t={t:.0f}ms  面板可见 {prev[0]}→{state[0]}  "
                    f"快照窗可见 {prev[1]}→{state[1]} 高 {prev[2]}→{state[2]}")
            prev = state
        step = max(1, len(samples) // 16)
        say("  时间线 (t, 面板可见, 快照窗可见, 快照窗高, 块数, 旧图alpha, 旧图可见, 文本长度)：")
        for t, state in samples[::step]:
            say(f"    {t:7.1f}  {state}")

        return 0
    finally:
        for widget in (dialog, window):
            if widget is not None:
                try:
                    widget.close()
                except RuntimeError:
                    pass
        pump(app, 250)
        restore_settings(settings, snapshot)
        settings.sync()
        with open(OUT, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_lines))
        print(f"产物：{OUT}")


if __name__ == "__main__":
    sys.exit(main())
