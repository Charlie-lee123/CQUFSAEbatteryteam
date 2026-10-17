# -*- coding: utf-8 -*-
r"""本轮三项专项证据：B 排版 / D 最大化可见性 / E 设置面板锚点。

  B：N=7 / 8 / 13 三种数据量下的数据网格排版（截图 + 每排个数），验证
     "末排多一、其余各排一致、左对齐"。
  D：接收区最大化 / 退出全程**逐帧**采样，证明接收卡每帧都在屏上（不可见帧 = 0）
     且位移连续（无阶跃）。
  E：设置面板开→关的中间帧（截图 + 几何），证明起点=设置按钮、终点=设置按钮。

用法：
    python devtools\_probe_round2.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings                       # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter   # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton     # noqa: E402

OUT = os.path.join(_ROOT, "_check")


def pump(app, seconds: float, sleep: float = 0.0) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        if sleep:
            time.sleep(sleep)


# ------------------------------------------------------------------ B 排版

def check_grid(app) -> bool:
    """数据网格：末排多一、其余各排一致且左对齐。"""
    from ui_highlight import align_indexes, grid_row_sizes

    print("=" * 72)
    print("B 排版：每排个数（末排多一，其余一致）")
    print("=" * 72)
    ok = True
    cases = (7, 8, 13)
    for n in cases:
        # 设备把 N 个点**发在同一行**；用户配了「每模组电芯数 = 3」→ 每排 3 个，
        # 余数按"末排多一、其余一致"分配（cols_hint=3 走的就是配置生效的路径）。
        line = " ".join(f"{i}.3700" for i in range(1, n + 1))
        out = align_indexes(line, section="voltage", volt_cols_hint=3)
        rows = [r for r in out.split("\n") if r.strip()]
        sizes = [len(r.split()) for r in rows]
        expect = grid_row_sizes(n, 0, 3)
        diff = max(sizes) - min(sizes)
        tail_more = len(sizes) >= 2 and sizes[-1] >= sizes[-2]
        good = sizes == expect and diff <= 1 and tail_more
        ok = ok and good
        print(f"  N={n:2d} 每排个数={sizes} (期望 {expect}) "
              f"极差={diff} 末排>=前排={tail_more} -> {'OK' if good else 'FAIL'}")
        print(f"        首行: {rows[0]!r}")
        print(f"        末行: {rows[-1]!r}")
    # 附带验证"0 = 自动"时**不改变老行为**（设备发一行 N 个 → 仍是一行）
    for n in cases:
        line = " ".join(f"{i}.3700" for i in range(1, n + 1))
        auto = align_indexes(line, section="volt")
        auto_rows = [r for r in auto.split("\n") if r.strip()]
        same = len(auto_rows) == 1 and len(auto_rows[0].split()) == n
        ok = ok and same
        print(f"  自动档 N={n}: 排数={len(auto_rows)} -> "
              f"{'OK（老行为不变）' if same else 'FAIL'}")

    # 截图：把三种排版画成一张图存进 _check
    shots = []
    for n in cases:
        line = " ".join(f"{i}.3700" for i in range(1, n + 1))
        out = align_indexes(line, section="voltage", volt_cols_hint=3)
        shots.append((f"N={n}", out))
    font = QFont("Cascadia Mono", 12)
    fm = QFont("Cascadia Mono", 12)
    from PySide6.QtGui import QFontMetrics
    metrics = QFontMetrics(fm)
    width = max(max(metrics.horizontalAdvance(row) for row in text.split("\n"))
                for _t, text in shots) + 40
    pad, label_h = 16, 24
    line_h = metrics.height() + 4
    height = max(len(text.split("\n")) for _t, text in shots) * line_h + label_h + pad
    sheet = QImage(width + pad * 2, height + pad, QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#0D0D0F"))
    painter = QPainter(sheet)
    painter.setFont(font)
    x = pad
    for title, text in shots:
        painter.setPen(QColor("#F2F3F5"))
        painter.drawText(x, label_h - 6, title)
        y = label_h
        for line_ in text.split("\n"):
            painter.drawText(x, y + metrics.ascent(), line_)
            y += line_h
        x += width // len(shots) + 20
    painter.end()
    path = os.path.join(OUT, "grid_layout_n7_8_13.png")
    sheet.save(path, "PNG")
    print("  排版截图:", path)
    return ok


# ------------------------------------------------------------------ D 可见性

def check_visibility(app, window) -> bool:
    """最大化 / 退出全程：接收卡必须每帧都在屏上，且位移连续。"""
    print("\n" + "=" * 72)
    print("D 接收区可见性（逐帧采样）")
    print("=" * 72)
    from ui_anim import _ticker, ticker_active

    card = window._rx_card
    ok = True

    class _Sampler:
        """挂在动画引擎的滴答上采样：每个**动画帧**量一次，而不是按轮询间隔
        （轮询会一次跨好几帧，量出来的"位移"是好几帧之和，会误判成阶跃）。"""

        def __init__(self) -> None:
            self.frames = 0
            self.invisible = 0
            self.tops: list[int] = []
            self.on = False

        def attach(self) -> None:
            _ticker()._timer.timeout.connect(self.tick)

        def tick(self) -> None:
            if not self.on:
                return
            self.frames += 1
            if not card.isVisible() or card.height() <= 1:
                self.invisible += 1
            self.tops.append(card.mapTo(window, card.rect().topLeft()).y())

    sampler = _Sampler()
    sampler.attach()
    for label, trigger, seconds in (("最大化", window._expand_receive, 1.2),
                                    ("退出", window._collapse_receive, 1.2)):
        sampler.frames = 0
        sampler.invisible = 0
        sampler.tops = []
        sampler.on = True
        trigger()
        deadline = time.perf_counter() + seconds
        while ticker_active() and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.001)
        sampler.on = False
        deltas = [b - a for a, b in zip(sampler.tops, sampler.tops[1:])]
        steps = [abs(d) for d in deltas]
        jump = max(steps) if steps else 0
        # 「无阶跃」的三条判据：
        #   ① 不可见帧 = 0（用户报的"接收区消失一段时间"）
        #   ② 位移**单调**：全程只朝一个方向走，不来回抽
        #   ③ 单帧位移有界 —— 阈值取 60px：in_out_quint 的峰值速度是平均速度的
        #      约 5 倍，本例总位移 ~170px / 27 帧 → 理论峰值 ~31px，再留出全屏
        #      几何落地那一下的布局位移，60px 是"连续运动"与"跳一下"的分界。
        forward = all(d >= 0 for d in deltas)
        backward = all(d <= 0 for d in deltas)
        monotone = forward or backward
        good = sampler.invisible == 0 and monotone and jump <= 60
        ok = ok and good
        worst = sorted(((abs(b - a), i) for i, (a, b) in
                        enumerate(zip(sampler.tops, sampler.tops[1:]))),
                       reverse=True)[:3]
        print(f"  [{label}] 动画帧={sampler.frames} 不可见帧={sampler.invisible} "
              f"单调={monotone} 单帧最大位移={jump}px (阈值 60) -> "
              f"{'OK' if good else 'FAIL'}")
        print(f"        最慢三步(序号:位移) = "
              f"{', '.join(f'#{i}:{d}px' for d, i in worst)}")
        print(f"        顶部轨迹前 10 帧 = {sampler.tops[:10]}")
    return ok


# ------------------------------------------------------------------ E 锚点

def check_settings_anchor(app, window) -> bool:
    """设置面板：从设置按钮长出来、收回设置按钮。"""
    print("\n" + "=" * 72)
    print("E 设置面板锚点（起点/终点 = 设置按钮矩形）")
    print("=" * 72)
    btn = window._settings_button_rect()
    print(f"  设置按钮矩形: {btn.getRect()}")
    window._open_settings()
    opened: list = []
    for _ in range(14):
        app.processEvents()
        time.sleep(0.02)
        dialog = window._settings_dialog
        if dialog is not None:
            opened.append(dialog.geometry().getRect())
    first = opened[0] if opened else None
    near_start = bool(first) and abs(first[0] - btn.x()) <= 4 and abs(first[1] - btn.y()) <= 4
    print(f"  入场首帧: {first} （应贴着按钮左上）-> {'OK' if near_start else 'FAIL'}")

    dialog = window._settings_dialog
    ok = near_start
    if dialog is not None:
        buttons = [b for b in dialog.findChildren(QPushButton) if b.text() == "确定"]
        if buttons:
            buttons[0].click()
        closing: list = []
        for _ in range(20):
            app.processEvents()
            time.sleep(0.02)
            d = window._settings_dialog
            if d is not None:
                closing.append(d.geometry().getRect())
        last = closing[-1] if closing else None
        near_end = bool(last) and abs(last[0] - btn.x()) <= 4 and abs(last[1] - btn.y()) <= 4
        print(f"  收场末帧: {last} （应回到按钮）-> {'OK' if near_end else 'FAIL'}")
        ok = ok and near_end
    for _ in range(40):
        app.processEvents()
    closed = window._settings_dialog is None
    print(f"  动画结束后面板已关闭: {closed} -> {'OK' if closed else 'FAIL'}")
    return bool(ok) and closed


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_anim import set_animations_enabled_override
    from ui_theme import apply_scheme, ui_font

    set_animations_enabled_override(True)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    import customization
    from main_window import MainWindow

    for spec in customization.specs():
        customization.set(spec["key"], spec["default"])
    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    window = MainWindow()
    window._port_watch.stop()
    screen = app.primaryScreen().availableGeometry()
    window.setGeometry(screen.x() + 60, screen.y() + 40, 1180, 860)
    window.show()
    window.raise_()
    pump(app, 0.6)

    b_ok = check_grid(app)
    d_ok = check_visibility(app, window)
    e_ok = check_settings_anchor(app, window)

    print("\n" + "=" * 72)
    print(f"总判定: {'PASS' if (b_ok and d_ok and e_ok) else 'FAIL'} "
          f"(B={b_ok} D={d_ok} E={e_ok})")
    print("=" * 72)

    window.setGeometry(0, 0, 1134, 820)
    window.close()
    for key in list(settings.allKeys()):
        if key not in snapshot:
            settings.remove(key)
    for key, value in snapshot.items():
        if value is not None:
            settings.setValue(key, value)
    settings.sync()
    return 0 if (b_ok and d_ok and e_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
