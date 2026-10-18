# -*- coding: utf-8 -*-
r"""接收区「暂停 / 继续」逐帧体检：把这一段动画里每个几何量的每帧位移打出来。

为什么要单独一个探针：用户说"暂停的动画好像不够顺滑"，而暂停这一段同时有
三件事在改几何 —— ① 发送区补间折叠（240ms）；② 时间戳工具栏（opts 行）瞬间
hide；③ 暂停定位（滚动条一步跳到目标行）。②③ 都是**同步瞬间**完成的，肉眼
看到的就是"平滑的折叠里夹着两下硬跳"。

取样挂在 `ui_anim._Ticker._tick` 上（与动画同拍，见 `_probe_anim_frames.py`
的说明），只看这一段真正发生的位移。

用法：
    python devtools/_probe_pause_anim.py            # 静态填空数据
    python devtools/_probe_pause_anim.py --verbose  # 连未变化的帧一起打

产物：_check/pause_anim.txt
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import QPoint, QSettings                       # noqa: E402
from PySide6.QtWidgets import QApplication                        # noqa: E402

RAW = os.path.join(_ROOT, "_check", "pause_anim.txt")

# 被跟踪的量：名字 → 取样函数（返回一个可比的数）
METRICS = (
    ("sb", lambda w: w.txt_rx.verticalScrollBar().value()),
    ("vp_h", lambda w: w.txt_rx.viewport().height()),
    ("rx_y", lambda w: w.txt_rx.mapToGlobal(QPoint(0, 0)).y()),
    ("opts_h", lambda w: w._rx_opts_row.height()),
    ("opts_v", lambda w: 1 if w._rx_opts_row.isVisible() else 0),
    ("tx_h", lambda w: w._tx_card.height()),
    ("rx_h", lambda w: w._rx_card.height()),
    ("split", lambda w: w._splitter.sizes()[1]),
)


def _restore(settings, snapshot) -> None:
    for key in settings.allKeys():
        if key not in snapshot:
            settings.remove(key)
    for key, value in snapshot.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)


class Tap:
    def __init__(self, app, window):
        self.app = app
        self.window = window
        self.rows: list[list[float]] = []
        self._orig = None
        self.t0 = 0.0

    def _sample(self) -> list[float]:
        row = []
        for _name, fn in METRICS:
            try:
                row.append(fn(self.window))
            except (RuntimeError, AttributeError):
                row.append(0)
        return row

    def _grab(self) -> None:
        t = (time.perf_counter() - self.t0) * 1000.0
        self.rows.append([round(t, 1)] + self._sample())

    def __enter__(self):
        import ui_anim
        self._orig = ui_anim._ticker()._tick

        def _patched():
            self._orig()
            self._grab()

        ui_anim._ticker()._tick = _patched
        return self

    def __exit__(self, *exc):
        import ui_anim
        ui_anim._ticker()._tick = self._orig
        return False

    def run(self, action, settle_ms: float = 760.0):
        """返回 (动作前, 动作后同一瞬间, 逐帧)。

        ⛔ 中间那两条**必须**取：`_toggle_pause()` 里 `opts.hide()` 和
        `_snap_to_round_start()` 都是**同步**完成的，位移发生在第一个补间帧
        之前 —— 只看逐帧数据会以为"什么都没跳"。
        """
        self.rows.clear()
        before = self._sample()
        self.t0 = time.perf_counter()
        action()
        after = self._sample()
        deadline = time.perf_counter() + settle_ms / 1000.0
        while time.perf_counter() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.app.processEvents()
        return before, after, self.rows


def report(name: str, before, after, rows, verbose: bool = False) -> list[str]:
    out = [f"\n== {name} ==  帧数 {len(rows)}"]
    out.append("  【同步跳变】动作返回的同一瞬间（发生在第一个补间帧之前）")
    for k, (n, _fn) in enumerate(METRICS):
        d = after[k] - before[k]
        if d:
            mark = "  ← 硬跳" if abs(d) >= 4 else ""
            out.append(f"    {n:<7} {before[k]:>6} → {after[k]:>6}  ({d:+d}){mark}")
    if all(after[k] == before[k] for k in range(len(METRICS))):
        out.append("    （无）")
    if len(rows) < 3:
        out.append("  （没有动画帧）")
        return out
    span = rows[-1][0] - rows[0][0]
    out.append(f"  时长 {span:.0f}ms")
    header = "  t(ms)  dt  " + "  ".join(f"{n:>6}" for n, _ in METRICS)
    out.append(header)
    for i, row in enumerate(rows):
        if i == 0:
            dt = 0.0
            delta = [0] * len(METRICS)
        else:
            dt = row[0] - rows[i - 1][0]
            delta = [abs(row[1 + k] - rows[i - 1][1 + k]) for k in range(len(METRICS))]
        peak = max(delta)
        if verbose or peak > 0 or i < 3:
            cells = []
            for k in range(len(METRICS)):
                mark = "*" if delta[k] and peak and delta[k] >= max(1, peak * 0.6) else " "
                cells.append(f"{row[1 + k]:>6}{mark}")
            out.append(f"  {row[0]:>6.1f} {dt:>4.1f} " + "  ".join(cells))
    # 速度判据：每帧 delta / dt
    out.append("  -- 跳变候选（速度 > 0.6px/ms 且单帧 >= 8px）--")
    hits = []
    for i in range(1, len(rows)):
        dt = max(0.1, rows[i][0] - rows[i - 1][0])
        for k, (n, _fn) in enumerate(METRICS):
            d = abs(rows[i][1 + k] - rows[i - 1][1 + k])
            v = d / dt
            if d >= 8 and v > 0.6:
                hits.append((v, i, n, d, dt))
    if not hits:
        out.append("    （无）")
    for v, i, n, d, dt in sorted(hits, reverse=True)[:12]:
        out.append(f"    {n:<7} 帧{i:>3} {d:>5.0f}px / {dt:>4.1f}ms = {v:.2f}px/ms")
    return out


def main() -> int:
    verbose = "--verbose" in sys.argv
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))
    from ui_anim import display_max_hz, frame_hz, set_frame_hz
    set_frame_hz(display_max_hz())
    print(f"采样节拍 = {frame_hz()}Hz")

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}

    from main_window import MainWindow
    window = MainWindow()
    lines: list[str] = []
    try:
        window.show()
        window.apply_startup_state()
        for _ in range(120):
            app.processEvents()
            time.sleep(0.003)
        # 造数据：好几轮，滚动条范围足够大，暂停定位才有得跳
        window.chk_timestamp.setChecked(True)
        window._clear_receive()
        for r in range(14):
            frame = (f"Voltage{r}:\n"
                     + " ".join(f"{j}.3820" for j in range(1, 26)) + "\n\n"
                     + f"Temperature{r}:\n1.22 2.22 3.22 4.25\n\n")
            window._on_received(frame.encode("utf-8"))
            for _ in range(40):
                app.processEvents()
                time.sleep(0.004)
        window._flush_paused_frames() if window._paused else None
        for _ in range(150):
            app.processEvents()
            time.sleep(0.003)

        # 前提：发送区得是展开的，才看得到折叠动画
        if window._tx_collapsed:
            window._toggle_send_area()
            for _ in range(200):
                app.processEvents()
                time.sleep(0.003)

        with Tap(app, window) as tap:
            lines += report("暂停（折叠发送区 + 藏 opts 行 + 定位）",
                            *tap.run(lambda: window._toggle_pause()), verbose)
            for _ in range(150):
                app.processEvents()
                time.sleep(0.003)
            lines += report("继续（还原发送区 + 显示 opts 行 + 刷帧追底）",
                            *tap.run(lambda: window._toggle_pause()), verbose)

        text = "\n".join(lines)
        print(text)
        os.makedirs(os.path.dirname(RAW), exist_ok=True)
        with open(RAW, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"\n逐帧数据 → {RAW}")
    finally:
        window.setGeometry(0, 0, 1134, 820)
        window.close()
        app.processEvents()
        _restore(settings, snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
