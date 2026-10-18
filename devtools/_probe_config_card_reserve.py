# -*- coding: utf-8 -*-
r"""接收区转场专项探针：配置卡「预留开关位 + 内容不重排」（只读，不改源码）。

用户第三次反馈（原话）：
    「接收区最大化的动画做得很好了，但是**取消最大化**的动画还是有问题，
      串口配置卡片要**提前预留好串口开关的位置**，动画要**同步**，过程中
      **字体不要有干涉**，动画要**讲求逻辑性**。」

判据（每条都对着用户一句话）：
  A  卡内表单**不重排**（"字体不要有干涉"）
     · ``串口选择`` 标签的 ``width()`` 全程 max−min == 0
     · 表单容器 ``_config_content`` 宽度恒定
     · 标签/开关**相对卡片**的 (x,y,w,h) 全程不变
  B  卡片**提前到位**（"提前预留好串口开关的位置" + "讲求逻辑性"）
     · 宽度是 q 的单调不减函数（无回抽）
     · 宽度 == 满宽 × min(1, q/0.85)（形状可核对）
     · 退出段：q ≥ 0.45 时已满宽，且满宽时刻 ≤ 整段 60%
  C  开关与卡片**同步**
     · 落地零位移：最后一帧开关矩形 == 预留那一格（普通态静止值）
     · 没有"卡片比一个开关位还窄、开关却已经在卡片这一侧"的帧
     · 开关本体宽度全程恒定（飞行中不被拉伸）
  G  速度（"跳变"的量化）
     · G'：实测峰值速度 / 曲线的**理论峰值** ≤ 1.25×（真跳变才超标）
     · 参考：max/中位 与单帧最大位移（STANDARD 曲线的 max/中位 天然≈4）

另外把 ``brand_header`` / 工具栏内容的子控件几何也逐帧记一份，用于说明
"高度压缩时它们会不会重排/滑动"。

用法：
    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools\_probe_config_card_reserve.py
产物：_check/config_card_reserve.txt
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

from PySide6.QtCore import QPoint, QRect, QSettings                    # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

SPEED_RATIO_MAX = 3.0        # 参考用（STANDARD 天生产 ≈4）
CURVE_TOL = 1.25             # G'：实测峰值 / 理论峰值
AHEAD_EXPECT = 0.45          # B：q ≥ 0.45 时应已满宽
MIN_FRAME_MS = 3.5           # 与 QA 一致：更近的两次取样视为同一视觉帧
TRACK = (("card", "_config_card"), ("rx_card", "_rx_card"), ("txt_rx", "txt_rx"),
         ("tx_card", "_tx_card"), ("rail", "_rx_rail"), ("brand", "brand_header"),
         ("opts", "_rx_opts_row"))

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    return ok


def info(name: str, detail: str) -> None:
    print(f"INFO  {name}   [{detail}]")


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.001)


def _curve_peak_slope() -> float:
    """STANDARD 的峰值斜率（Δ进度 / Δ归一化时间）—— 与 QA 的算法一致。"""
    from ui_anim import STANDARD
    return max((STANDARD((k + 1) / 2000.0) - STANDARD(k / 2000.0)) * 2000.0
               for k in range(2000))


class FrameTap:
    """把取样挂在 ``ui_anim._Ticker._tick`` 上，且**只在这条补间真的推进时**记一帧。"""

    def __init__(self, window) -> None:
        self.window = window
        self.rows: list[tuple[float, dict]] = []
        self._recording = False
        self._last_t = None
        self._orig = None
        self._ticker = None

    def _abs(self, widget) -> tuple[int, int, int, int]:
        p = widget.mapTo(self.window, QPoint(0, 0))
        return (p.x(), p.y(), widget.width(), widget.height())

    def _rel(self, widget, anchor) -> tuple[int, int, int, int]:
        """相对某个容器的矩形：容器自己移动时子控件跟着走是正常的，
        要恒定的是"相对容器的偏移 + 尺寸"。"""
        p = widget.mapTo(anchor, QPoint(0, 0))
        return (p.x(), p.y(), widget.width(), widget.height())

    def _sample(self) -> dict:
        w = self.window
        try:
            snap = {
                "t": float(w._rx_t),          # 0=普通, 1=最大化（即 p）
                "card": self._abs(w._config_card),
                "card_vis": 1 if w._config_card.isVisible() else 0,
                "content_w": w._config_content.width(),
                "label_w": w._port_label.width(),
                # 可见的开关本体（btn_open）。_open_row 是个 262px 宽的布局行
                # （开关 + stretch），它的包围盒右沿会伸进卡片里，但那块是空的；
                # 用户看到的"开关"只有 btn_open 这 110px。
                "switch": self._abs(w.btn_open),
                "slot": w._rx_card_slot_w(),
                "inner_lbl": self._rel(w._port_label, w._config_card),
                "opts_child": self._rel(w.chk_timestamp, w._opts_content),
                "brand_child": None,
            }
            for short, attr in TRACK:
                widget = getattr(w, attr, None)
                snap[short] = self._abs(widget) if widget is not None else None
        except RuntimeError:
            return {}
        return snap

    def __enter__(self):
        import ui_anim
        self._ticker = ui_anim._ticker()
        self._orig = self._ticker._tick

        def patched():
            self._orig()
            if not self._recording:
                return
            # ⚠️ 只在**接收区补间真的推进了一帧**时取样：全局 ticker 按显示器频率
            #    （165Hz）跑，这条补间被 min_interval_ms 节流到 ≈60fps —— 每次都采
            #    会把"几何没变"的中间 tick 混进来，位移摊到错误的小 dt 上，速度被
            #    算大好几倍（假跳变）。
            try:
                cur_t = float(self.window._rx_t)
            except RuntimeError:
                return
            if cur_t == self._last_t:
                return
            self._last_t = cur_t
            snap = self._sample()
            if snap:
                self.rows.append((time.perf_counter(), snap))

        self._ticker._tick = patched
        return self

    def __exit__(self, *_exc):
        if self._ticker is not None and self._orig is not None:
            self._ticker._tick = self._orig
        return False

    def run(self, action, max_ms: float = 1600.0):
        """录一段转场：从 action 起，到**接收区补间自己停下**为止。

        ⚠️ 不能等 ``ticker_active()``：退场时 ``_rx_highlighter.defer(420)`` 会在
        转场之后接着跑自己的补间，把 265ms 的"什么都没动"的尾巴录进来 —— 中位
        速度被稀释，max/中位 会算成十几倍（假报警）。
        """
        self.rows = []
        t0 = time.perf_counter()
        action()
        self._recording = False
        self._last_t = None
        snap0 = self._sample()            # 动作刚做完、第一帧之前
        if snap0:
            self.rows.append((time.perf_counter(), snap0))
            self._last_t = snap0["t"]
        self._recording = True
        app = QApplication.instance()
        while (time.perf_counter() - t0) * 1000.0 < max_ms:
            app.processEvents()
            tween = getattr(self.window, "_rx_tween", None)
            if tween is not None and not tween.running and len(self.rows) >= 2:
                break
            time.sleep(0.001)
        self._recording = False
        app.processEvents()
        return self.rows


def merge(rows, min_dt_ms: float = MIN_FRAME_MS):
    """把间隔 < min_dt_ms 的相邻取样合并成一帧（同 QA 的理由）。"""
    if len(rows) < 2:
        return list(rows)
    out = [rows[0]]
    for t, snap in rows[1:]:
        if (t - out[-1][0]) * 1000.0 >= min_dt_ms:
            out.append((t, snap))
        else:
            out[-1] = (out[-1][0], snap)
    return out


def overlaps(a, b) -> bool:
    return QRect(*a).intersects(QRect(*b))


def analyse(window, label: str, rows, expanding: bool, slot_ref) -> dict:
    merged = merge(rows)
    if len(merged) < 4:
        check(f"{label}/取样", False, f"只有 {len(merged)} 帧")
        return {}
    span_ms = (merged[-1][0] - merged[0][0]) * 1000.0
    fps = 1000.0 * (len(merged) - 1) / max(1.0, span_ms)
    info(f"{label}/帧", f"{len(merged)} 帧 / {span_ms:.0f}ms（≈{fps:.0f}fps）")

    # ---------------- A：卡内表单不重排 ----------------
    label_ws = [s["label_w"] for _t, s in merged]
    content_ws = [s["content_w"] for _t, s in merged]
    check(f"A/{label}·串口选择 标签宽度 max−min == 0",
          max(label_ws) - min(label_ws) == 0,
          f"{min(label_ws)}~{max(label_ws)}px（{len(label_ws)} 帧）")
    check(f"A/{label}·表单容器宽度 max−min == 0",
          max(content_ws) - min(content_ws) == 0,
          f"{min(content_ws)}~{max(content_ws)}px")
    for key, nice in (("inner_lbl", "串口选择 标签"), ("opts_child", "显示时间戳 开关")):
        gs = [s[key] for _t, s in merged]
        moved = max(max(g[k] for g in gs) - min(g[k] for g in gs) for k in range(4))
        check(f"A/{label}·{nice} 相对容器的 (x,y,w,h) 全程恒定", moved == 0,
              f"最大变化 {moved}px")
        if moved:
            info(f"A/{label}·{nice} 逐帧",
                 " ".join(f"t={s['t']:.2f}:{s[key][1]}" for _t, s in merged))

    # ---------------- B：卡片提前到位 ----------------
    # ⚠️ 口径：``_rx_t`` 是 tween 的值，0=普通态 / 1=最大化，也就是 p；
    #    q（"普通程度"）= 1 − p，卡片宽度只看 q。
    ahead = float(getattr(type(window), "_RX_CARD_AHEAD", 0.85))
    vis = [(1.0 - s["t"], s["card"][2]) for _t, s in merged if s["card_vis"]]
    full = max(w for _q, w in vis) if vis else 0
    mono_bad = []
    for (q1, w1), (q2, w2) in zip(vis, vis[1:]):
        if q2 > q1 and w2 < w1 - 1:
            mono_bad.append((q1, q2, w1, w2))
        elif q2 < q1 and w2 > w1 + 1:
            mono_bad.append((q1, q2, w1, w2))
    check(f"B/{label}·卡片宽度随 q 单调（无回抽）", not mono_bad,
          f"满宽 {full}px；违例 {len(mono_bad)} 处")
    shape_bad = []
    for q, cw in vis:
        want = round(full * min(1.0, max(0.0, q) / ahead))
        floored = cw >= window._rx_card_slot_w() - 1 and want < window._rx_card_slot_w()
        # 容差 4px：q 极小时满宽×比值只有几像素，取整误差占比大，视觉无意义
        if not floored and abs(cw - want) > 4:
            shape_bad.append((q, cw, want))
    check(f"B/{label}·宽度 == 满宽×min(1,q/{ahead:g})", not shape_bad,
          f"违例 {len(shape_bad)} 帧" +
          (f"；首例 q={shape_bad[0][0]:.2f} 实测 {shape_bad[0][1]} 期望 {shape_bad[0][2]}"
           if shape_bad else ""))
    arrive_q = arrive_ms = None
    t_start = merged[0][0]
    for (ts, s) in merged:
        if s["card_vis"] and s["card"][2] >= full - 1:
            arrive_q = 1.0 - s["t"]
            arrive_ms = (ts - t_start) * 1000.0
            break
    if arrive_q is None:
        check(f"B/{label}·到过满宽", False, "整段没到过满宽")
    else:
        info(f"B/{label}·满宽到位",
             f"q={arrive_q:.2f} / {arrive_ms:.0f}ms（占整段 "
             f"{arrive_ms / span_ms * 100:.0f}%）")
        if not expanding:
            check(f"B/{label}·q≥{AHEAD_EXPECT:g} 时已满宽",
                  arrive_q >= AHEAD_EXPECT - 1e-6,
                  f"满宽 {full}px 到位时 q={arrive_q:.2f} / {arrive_ms:.0f}ms")
            check(f"B/{label}·满宽到位不晚于整段 60%",
                  arrive_ms <= span_ms * 0.60,
                  f"{arrive_ms:.0f}ms / {span_ms:.0f}ms"
                  f"（{arrive_ms / span_ms * 100:.0f}%）")

    # ---------------- C：开关与卡片同步 ----------------
    # （落地零位移在调用方量：拿"落定后"的真实矩形和末帧比，见 main）
    # C1 没有"卡片比一个开关位还窄、开关却已在这侧"的帧
    poke = [(1.0 - s["t"], s["switch"][2], s["card"][2]) for _t, s in merged
            if s["t"] <= 0.5 and s["card_vis"] and s["card"][2] < s["slot"] - 1
            and s["switch"][2] > s["card"][2] + 1]
    check(f"C/{label}·没有卡片比开关还窄的帧（开关会戳出卡外）", not poke,
          f"违例 {len(poke)} 帧" +
          (f"；首例 q={poke[0][0]:.2f} 开关 {poke[0][1]}px / 卡片 {poke[0][2]}px"
           if poke else ""))
    # C2 开关本体宽度恒定
    sw_ws = {s["switch"][2] for _t, s in merged}
    check(f"C/{label}·开关本体宽度全程恒定", len(sw_ws) == 1, f"宽度集合 {sorted(sw_ws)}")
    # C3 退出方向：末帧开关的左上角必须已经落在**预留那一格**的左上角
    if not expanding and slot_ref is not None:
        last_sw = merged[-1][1]["switch"]
        gap = max(abs(last_sw[0] - slot_ref[0]), abs(last_sw[1] - slot_ref[1]))
        check(f"C/{label}·末帧开关已落在预留格左上角", gap <= 1,
              f"末帧 ({last_sw[0]},{last_sw[1]}) / 预留格 ({slot_ref[0]},{slot_ref[1]})"
              f"，差 {gap}px")
    # 参考信息：飞行途中开关矩形与卡片矩形有交叠的帧数
    # （功能列就在配置卡左邻，开关的落点又在卡内，路径必然掠过卡片区域 ——
    #   它是浮在上层的飞行元素，交叠本身是预期行为，只作为数字记录）
    cross = sum(1 for _t, s in merged if s["t"] > 0.05 and s["card_vis"]
                and overlaps(s["switch"], s["card"]))
    info(f"C/{label}·飞行途中与卡片矩形交叠", f"{cross}/{len(merged)} 帧（预期内）")

    # ---------------- G：速度 ----------------
    peak = _curve_peak_slope()
    duration = float(getattr(window, "_RX_TRANSITION_MS", 260))
    for short, _attr in TRACK:
        speeds, deltas, dts = [], [], []
        first = last = None
        for (t1, s1), (t2, s2) in zip(merged, merged[1:]):
            a, b = s1.get(short), s2.get(short)
            if a is None or b is None:
                continue
            if first is None:
                first = a
            last = b
            dt = max(0.1, (t2 - t1) * 1000.0)
            d = max(abs(b[k] - a[k]) for k in range(4))
            speeds.append(d / dt)
            deltas.append(d)
            dts.append(dt)
        moving = [v for v in speeds if v > 0.005]
        if len(moving) < 3 or first is None or last is None:
            continue
        # "行程"取**逐帧位移之和**（走的路径长），不是首末净位移：工具栏那一行
        # 一边缩小一边被整页带着上移，两者部分抵消，净位移会低估它真实的运动量，
        # 拿净位移算出来的"理论峰值"就成了一个偏小的假基准。单调运动两者相等，
        # 所以对其它控件这条判据的口径没有变。
        travel = sum(deltas)
        worst = max(speeds)
        idx = speeds.index(worst)
        med = statistics.median(moving)
        theoretical = peak * travel / duration if travel >= 8 else 0.0
        ratio = worst / theoretical if theoretical > 0 else 0.0
        check(f"G'/{label}·{short} 峰值速度 ≤ 理论峰值×{CURVE_TOL:g}", ratio <= CURVE_TOL,
              f"实测 {worst:.2f} / 理论 {theoretical:.2f} = {ratio:.2f}×"
              f"（行程 {travel}px，峰值帧 {deltas[idx]:.0f}px/{dts[idx]:.1f}ms）")
        info(f"G/{label}·{short}",
             f"max={worst:.2f}px/ms med={med:.2f} max/中位={worst / med:.2f}"
             f"（STANDARD 天然≈4，仅供参考）")
    return {"full": full, "arrive_ms": arrive_ms, "span_ms": span_ms}


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))

    from ui_anim import display_max_hz, frame_hz, set_frame_hz
    default_hz = frame_hz()
    hi_hz = display_max_hz()
    set_frame_hz(hi_hz)
    print(f"取样节拍 = {hi_hz}Hz（显示器上限）；STANDARD 峰值斜率 {_curve_peak_slope():.2f}")

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    from main_window import MainWindow
    window = MainWindow()
    window._port_watch.stop()
    window.setGeometry(0, 0, 1180, 860)
    window.show()
    # 用用户真实起手状态量（配置 startup_maximized=true → 开机即全屏），
    # 否则"展开接收区"会连窗口一起从 1180x860 跳到全屏，混进 OS 级几何变化。
    window.apply_startup_state()
    pump(app, 1.2)
    print(f"起手 fullscreen={window.isFullScreen()}  窗口 "
          f"{window.geometry().width()}x{window.geometry().height()}")
    # 普通态的"开关那一格"（后面每条 C1 都拿它当基准）
    slot_ref = None
    if not window._expanded:
        row = window._open_row
        p0 = row.mapTo(window, QPoint(0, 0))
        slot_ref = (p0.x(), p0.y(), row.width(), row.height())
        print(f"普通态预留格 = {slot_ref}")

    lines: list[str] = []
    try:
        with FrameTap(window) as tap:
            for cycle in (1, 2):
                if window._expanded:
                    tap.run(window._collapse_receive)
                    pump(app, 0.4)
                rows = tap.run(window._expand_receive)
                pump(app, 0.5)
                analyse(window, f"展开#{cycle}", rows, expanding=True, slot_ref=slot_ref)
                landed = tap._sample()          # 落定后（开关已在功能列里）的真实矩形
                jump = max(abs(landed["switch"][k] - rows[-1][1]["switch"][k])
                           for k in range(4))
                check(f"展开#{cycle}·落地零位移（末帧 → 落定后）", jump <= 1,
                      f"末帧 {rows[-1][1]['switch']} / 落定后 {landed['switch']}"
                      f"，最大差 {jump}px")
                last_w = rows[-1][1]["card"][2]
                check(f"展开#{cycle}·落定后卡片宽度 == 最后一帧宽度",
                      window._config_card.width() == last_w,
                      f"落定后 {window._config_card.width()}px / 末帧 {last_w}px")
                rows = tap.run(window._collapse_receive)
                pump(app, 0.5)
                st = analyse(window, f"退出#{cycle}", rows, expanding=False,
                             slot_ref=slot_ref)
                landed = tap._sample()          # 落定后（开关已插回卡片那一格）
                jump = max(abs(landed["switch"][k] - rows[-1][1]["switch"][k])
                           for k in range(4))
                check(f"退出#{cycle}·落地零位移（末帧 → 落定后）", jump <= 1,
                      f"末帧 {rows[-1][1]['switch']} / 落定后 {landed['switch']}"
                      f"，最大差 {jump}px")
                # 卡内版式落地也不许动（占位撤掉、真开关插回同一格）
                form = max(abs(landed["inner_lbl"][k] - rows[0][1]["inner_lbl"][k])
                           for k in range(4))
                check(f"退出#{cycle}·卡内版式落地零位移", form == 0,
                      f"首帧 {rows[0][1]['inner_lbl']} / 落定后 {landed['inner_lbl']}"
                      f"，最大差 {form}px")
                # 开关必须回到「串口配置」卡内部
                node = window._open_row.parentWidget()
                back = False
                while node is not None:
                    if node is window._config_card:
                        back = True
                        break
                    node = node.parentWidget()
                check(f"退出#{cycle}·落定后开关回到配置卡内", back,
                      f"父控件 {type(window._open_row.parentWidget()).__name__}")
                # 卡片里"预留那一格"是否一直存在（占位没被清掉/没多出来）
                gap = window._rx_home_gap
                check(f"退出#{cycle}·落定后占位已撤（不占两格）", gap is None, f"{gap!r}")
                lines.append(f"--- cycle {cycle} ---")
                lines.append(f"退出段 满宽={st.get('full')}px 满宽到位="
                             f"{st.get('arrive_ms')}ms / {st.get('span_ms'):.0f}ms")
    finally:
        try:
            if window._expanded:
                window._collapse_receive()
                pump(app, 1.0)
        except Exception:                       # noqa: BLE001
            pass
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
        for key, value in snapshot.items():
            if value is not None:
                settings.setValue(key, value)
        settings.sync()

    try:
        os.makedirs(os.path.join(_ROOT, "_check"), exist_ok=True)
        with open(os.path.join(_ROOT, "_check", "config_card_reserve.txt"),
                  "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass

    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    print("\n" + "=" * 96)
    for name, ok, detail in _RESULTS:
        print(f"{'PASS' if ok else 'FAIL':<6}{name:<50}{detail[:42]}")
    print("-" * 96)
    print(f"合计 {passed}/{total} PASS"
          f"（G' 峰值 ≤ 理论峰值×{CURVE_TOL:g}；G max/中位 仅参考，标准曲线天然≈4）")
    print("=" * 96)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
