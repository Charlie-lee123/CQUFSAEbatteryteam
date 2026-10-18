"""exit#1 定向复现（lead 2026-10-19 派活）：「开过设置面板后再退出最大化」
工具栏行的逐帧几何。**纯控件读数，不需要桌面**（锁屏也能跑）。

背景：run7（e4f9fd79）FINAL 里退出#1 出过 Δq==0 且 48px 的结构跳变签名，
退出#2/#3 干净；GEXIT（无前奏）在新 build 上三轮全干净。要回答的是：这个
48px 是「P+P2 前奏依赖的真瞬态（用户可见，必修）」还是「某一种可指名的
污染」。判据（lead 定）：工具栏行 y 的逐帧序列 + ``_rx_t`` 的逐帧值，
存在「``_rx_t`` 不变而 y 跳 ≥4px」的帧 → 真跳变；没有 → 污染，但必须指明
污染源。

结构事实（main_window.py，本次查实）：
  · 启动最大化 = ``startup_fullscreen()``：**窗口级**全屏，``_expanded`` 仍
    是 False、``_rx_full`` 仍是空、``_rx_t`` 仍是 0（:2135-2152）；
  · ``_rx_full``（brand_h/opts_h/rail_w/config_w）只在**展开且 t≤0.01** 时
    现测（``_rx_transition`` :2326-2332）——退出沿用上次展开量的那批；
  · ``_rx_prepare`` 每次转场现测 ``_rx_total = sum(splitter.sizes())``
    （:2431）——**测量那一刻的布局状态**决定这批数；
  · 设置面板关闭时 ``_apply_settings_result`` 在收场动画结束时才跑
    （``_on_settings_geometry_done`` 关闭分支）——「关面板」本身触发一次
    布局还原（按钮显隐/字号/帧率全量还回去）。
→ FINAL 与 GEXIT 的结构差被定位在：**expand 之前有没有那段设置前奏**
（FINAL：boot→P→P2→expand→exit#1；GEXIT：boot→expand→exit#1）。

变体（全部从 boot-fullscreen 起手，与两个正式跑法同构）：
  V0 GEXIT 复刻：expand → exit×3（干净对照）
  V1 FINAL 复刻：开关面板×2（不改任何东西）→ expand → exit×3
  V2 H1 按钮预览：开面板→预览藏 4 颗按钮→取消→再开关一次→expand→exit×3
  V3 H2 字号预览：开面板→预览字号 16→取消→再开关一次→expand→exit×3
  V4 H3 帧率预览：开面板→预览 60fps→取消→再开关一次→expand→exit×3（最后跑）

每个 exit 记双源序列：补间**交付帧**（``_on_rx_transition`` 包一层）+ 3ms
**轮询**（抓帧间布局事件）；另记该次展开量的 ``_rx_full`` 与每次
``_rx_prepare`` 的 ``_rx_total``。判定：
  · Δq==0 且 Δrow.y≥4px 的轮询对 → lead 判据的「真跳变」签名；
  · 该对之间有没有补间交付：没有 → 帧间布局事件（H4 类污染/真瞬态）；
  · 跨变体比 ``_rx_full``/``_rx_total`` → 前奏是否改变了转场的输入。

跑法：.venv/Scripts/python.exe devtools/_qa_exit1_preamble.py
"""
from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtCore import QTimer          # noqa: E402

import ui_anim                             # noqa: E402
import _qa_anim_verdict as qa               # noqa: E402  复用起手/泵/输出

POLL_MS = 3                 # 轮询间隔（比 16ms 节流密，抓得到帧间布局事件）
DQ_EPS = 1e-6               # 「_rx_t 没变」的判定阈值
JUMP_PX = 4                 # lead 的结构跳变门槛（>4px）
HIDE_BUTTONS = ["chk_timestamp", "chk_hex_view", "btn_copy_all", "btn_save"]


class _Recorder:
    """双源逐帧记录：补间交付帧 + 3ms 轮询；另快照 _rx_full/_rx_total。"""

    def __init__(self, app, window) -> None:
        self.app = app
        self.window = window
        self.polls: list[tuple] = []        # (t, q, y, h, brand_h, total)
        self.deliveries: list[tuple] = []   # (t, q, y, h, brand_h)
        self.full_at_capture: dict | None = None
        self.total_at_prepare: list[tuple[float, bool, int]] = []
        self._timer = QTimer()
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._saved: list[tuple[str, object]] = []
        self._orig_cb = None
        self._orig_tween_cb = None

    # -- 采样 ----------------------------------------------------------
    def _row_y(self) -> tuple[float, float, float, float]:
        """(row 全局 y, row 高, brand_header 高, _rx_total)。"""
        w = self.window
        try:
            y = float(w._rx_opts_row.mapToGlobal(w._rx_opts_row.rect().topLeft()).y())
        except (AttributeError, RuntimeError):
            y = float("nan")
        try:
            h = float(w._rx_opts_row.height())
        except (AttributeError, RuntimeError):
            h = float("nan")
        try:
            bh = float(w.brand_header.height())
        except (AttributeError, RuntimeError):
            bh = float("nan")
        try:
            total = int(w._rx_total)
        except (AttributeError, RuntimeError, ValueError):
            total = -1
        return y, h, bh, total

    def _poll(self) -> None:
        try:
            q = float(self.window._rx_t)
        except (AttributeError, RuntimeError):
            q = float("nan")
        y, h, bh, total = self._row_y()
        self.polls.append((time.perf_counter(), q, y, h, bh, total))

    # -- 挂钩 ----------------------------------------------------------
    def install(self) -> None:
        w = self.window
        rec = self

        def on_transition(t):
            self._orig_cb(t)
            y, h, bh, _total = self._row_y()
            try:
                q = float(w._rx_t)
            except (AttributeError, RuntimeError):
                q = float("nan")
            rec.deliveries.append((time.perf_counter(), q, y, h, bh))

        self._orig_cb = w._on_rx_transition
        w._on_rx_transition = on_transition
        self._saved.append(("_on_rx_transition", self._orig_cb))
        tween = getattr(w, "_rx_tween", None)
        if tween is not None:
            self._orig_tween_cb = tween._on_value
            tween._on_value = on_transition
            self._saved.append(("__tween__", self._orig_tween_cb))

        def on_capture():
            self._orig_capture()
            self.full_at_capture = dict(getattr(w, "_rx_full", {}) or {})

        self._orig_capture = w._rx_capture_full
        w._rx_capture_full = on_capture
        self._saved.append(("_rx_capture_full", self._orig_capture))

        def on_prepare(to_expanded):
            self._orig_prepare(to_expanded)
            try:
                total = int(w._rx_total)
            except (AttributeError, RuntimeError, ValueError):
                total = -1
            rec.total_at_prepare.append((time.perf_counter(),
                                         bool(to_expanded), total))

        self._orig_prepare = w._rx_prepare
        w._rx_prepare = on_prepare
        self._saved.append(("_rx_prepare", self._orig_prepare))
        self._timer.start()

    def uninstall(self) -> None:
        self._timer.stop()
        w = self.window
        for name, value in reversed(self._saved):
            if name == "__tween__":
                tween = getattr(w, "_rx_tween", None)
                if tween is not None:
                    tween._on_value = value
            else:
                if name in w.__dict__:
                    setattr(w, name, value)
                else:
                    w.__dict__.pop(name, None)
        self._saved.clear()

    # -- 一段退出 ------------------------------------------------------
    def run_exit(self) -> None:
        self.polls.clear()
        self.deliveries.clear()
        self.total_at_prepare.clear()
        self.window._collapse_receive()
        deadline = time.perf_counter() + 2.5
        started = False
        while time.perf_counter() < deadline:
            self.app.processEvents()
            active = ui_anim.ticker_active()
            started = started or active
            if started and not active:
                break
            time.sleep(0.001)
        self.app.processEvents()


def _settings_session(app, window, preview=None) -> None:
    """开设置面板 →（可选预览改动）→ 取消关闭 → 等收场动画+布局还原跑完。

    与 FINAL 里 P/P2 场景同构：开、等落定、关；预览改动走**真实回调路径**
    （``_preview_buttons`` / ``_preview_custom``，面板开关就是这么进来的）。
    """
    if window._settings_dialog is not None:
        qa.close_settings(app, window)
    window.btn_settings.click()
    qa.pump_until(app, lambda: window._settings_dialog is not None, 1500)
    qa.pump(app, 0.45)                     # 入场动画 + 换帧渐隐
    if preview is not None:
        preview(window)
        qa.pump(app, 0.25)
    qa.close_settings(app, window)         # 取消：收场动画结束才还原预览
    qa.pump(app, 0.35)                     # 还原后的布局稳定时间（同 FINAL 间隔）


def _analyse_exit(label: str, rec: _Recorder) -> dict:
    """一段退出的判定：Δq==0 且 Δy≥4 的轮询对（lead 判据的真跳变签名）。"""
    polls = rec.polls
    jumps = []
    for (t1, q1, y1, _h1, _b1, _tot1), (t2, q2, y2, _h2, _b2, _tot2) \
            in zip(polls, polls[1:]):
        if q1 != q1 or q2 != q2:           # NaN 防御
            continue
        if abs(q2 - q1) < DQ_EPS and abs(y2 - y1) >= JUMP_PX:
            delivered = any(t1 < dt < t2 for dt, *_ in rec.deliveries)
            jumps.append({"t1": t1, "t2": t2, "q": q1,
                          "dy": y2 - y1, "delivered_between": delivered})
    # 首帧跳变：退出前最后一个轮询 vs 第一个轮询（含 _rx_prepare 那一跳）
    first_jump = float("nan")
    if len(polls) >= 2:
        first_jump = polls[1][2] - polls[0][2]
    ys = [p[2] for p in polls if p[2] == p[2]]
    span = (max(ys) - min(ys)) if ys else float("nan")
    info = (f"{len(polls)}  polls / {len(rec.deliveries)} 交付帧；"
            f"Δq==0&Δy≥{JUMP_PX}px 共 {len(jumps)} 处"
            + (f"（最大 {max(abs(j['dy']) for j in jumps):.0f}px）" if jumps else "")
            + f"；首帧跳变 {first_jump:+.0f}px；全程 y 跨度 {span:.0f}px")
    qa.info(f"{label}", info)
    for j in jumps[:6]:
        qa.info(f"{label}/跳变明细",
                f"@{j['t1']:.3f}→{j['t2']:.3f}s（Δt={1000 * (j['t2'] - j['t1']):.0f}ms）"
                f"q={j['q']:.4f} 不变，row.y 跳 {j['dy']:+.0f}px；"
                f"两拍间有补间交付：{'是' if j['delivered_between'] else '否'}"
                f"（否=帧间布局事件，H4 类）")
    return {"jumps": jumps, "first_jump": first_jump, "span": span,
            "n_polls": len(polls), "n_deliv": len(rec.deliveries)}


def _variant(app, window, rec: _Recorder, code: str, name: str,
             sessions: int, previews: list | None) -> list[dict]:
    """一个变体：若干次设置前奏 → expand（现测 _rx_full）→ exit×3。"""
    for i in range(sessions):
        prev = previews[i] if previews and i < len(previews) else None
        _settings_session(app, window, prev)
    # expand：从普通态起跑，_rx_capture_full 在此现测（记录在 rec.full_at_capture）
    rec.full_at_capture = None
    window._expand_receive()
    qa.pump_until(app, lambda: not ui_anim.ticker_active(), 2000)
    qa.pump(app, 0.4)
    full = dict(rec.full_at_capture or {})
    qa.info(f"{code}/{name}/展开现测 _rx_full",
            "；".join(f"{k}={v}" for k, v in sorted(full.items())) or "（没抓到）")
    out = []
    for k in (1, 2, 3):
        rec.run_exit()
        res = _analyse_exit(f"{code}/退出#{k}", rec)
        res["full"] = dict(full)
        res["total_at_prepare"] = list(rec.total_at_prepare)
        out.append(res)
    return out


def main() -> int:
    app, window, ctx = qa._boot_single()
    rec = _Recorder(app, window)
    rec.install()
    results: dict[str, list[dict]] = {}
    try:
        qa.info("起手状态",
                f"fullscreen={window.isFullScreen()} expanded={window._expanded} "
                f"_rx_t={window._rx_t} _rx_full={window._rx_full}")
        variants = [
            ("V0", "GEXIT复刻（无前奏）", 0, None),
            ("V1", "FINAL复刻（开关面板×2）", 2, None),
            ("V2", "H1 按钮显隐预览", 2,
             [lambda w: w._preview_buttons(list(HIDE_BUTTONS)), None]),
            ("V3", "H2 字号预览", 2,
             [lambda w: w._preview_custom("rx_font_size", 16.0), None]),
            ("V4", "H3 帧率预览", 2,
             [lambda w: w._preview_custom("anim_fps", 60), None]),
        ]
        for code, name, sessions, previews in variants:
            # 每个变体从普通态起手（上个变体的 exit 已把它收回普通态）
            if window._expanded:
                window._collapse_receive()
                qa.pump(app, 1.0)
            results[code] = _variant(app, window, rec, code, name,
                                     sessions, previews)
            qa.pump(app, 0.3)
    finally:
        rec.uninstall()
        qa._teardown_single(app, window, ctx)

    # ---- 汇总：哪个变体复现了「q 不变 y 跳 ≥4px」
    print("\n" + "=" * 96)
    print("exit#1 定向复现 · 跨变体汇总（判据：Δq==0 且 Δrow.y≥4px）")
    print("=" * 96)
    for code, rows in results.items():
        for i, r in enumerate(rows, 1):
            n = len(r["jumps"])
            worst = max((abs(j["dy"]) for j in r["jumps"]), default=0.0)
            verdict = "干净" if n == 0 else f"复现 {n} 处 / 最大 {worst:.0f}px"
            print(f"{code} 退出#{i}: {verdict:<18} "
                  f"_rx_full(opts_h/brand_h)="
                  f"{r['full'].get('opts_h')}/{r['full'].get('brand_h')} "
                  f"_rx_total@prepare={[t for _t, _e, t in r['total_at_prepare']]}")
    print("-" * 96)
    dirty = {c: sum(len(r["jumps"]) for r in rows) for c, rows in results.items()}
    print("各变体跳变总数：" + "；".join(f"{c}={n}" for c, n in dirty.items()))
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
