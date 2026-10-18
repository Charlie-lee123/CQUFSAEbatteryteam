# -*- coding: utf-8 -*-
r"""动效跳变探针：逐帧量"发送区折叠/展开、接收区最大化/退出"的屏幕几何。

要回答的问题：用户反馈的"跳变"到底发生在**哪一帧、哪个控件、跳多少**。
帧率探针（_probe_fps.py）已证明常规场景不掉帧（>20ms 帧 = 0、P95 ≥ 120fps），
所以这里的对象不是帧率，而是**运动本身的连贯性**：

  ① 孤立阶跃：帧间位移 > 2px 且前后帧都基本不动（位置/尺寸瞬移）
  ② 非单调：序列先增后减再增（"鼓一下再缩"）
  ③ 显隐翻转：isVisible 在某一帧突然变（元素瞬间出现/消失）
  ④ 同步差：本应同拍到达的元素，到达时间差 > 40ms
  ⑤ reparent 跳变：串口开关搬家（240ms 定时器）前后的位置跳变

采集的过程（每个过程先落定，动画期间采到落定后 0.35s）：
  P1 发送区展开（手动）
  P2 发送区折叠（手动）
  P3 接收区最大化 —— 发送区**展开态**（_maximize_shrink_send 分支）
  P4 退出最大化   —— 发送区**展开态**（_tx_shrink.to(True) 分支）
  P5 接收区最大化 —— 发送区**折叠态**（默认开机场景，Reveal 54→0 分支）
  P6 退出最大化   —— 发送区**折叠态**（默认开机场景）
  P7 接收区最大化 —— **普通窗口**（_pre_expand_fullscreen=False，窗口要变大）
  P8 退出最大化   —— **普通窗口**（showNormal + 还原几何那一支）

起手与 main.py 一致：Fusion + ui_font(12.5) + apply_scheme；show() 后调
apply_startup_state()（默认"启动时最大化"= 整窗全屏，和真实启动一致）。

⚠️ 不许与其它探针并行跑（QSettings 竞态）。收尾会还原 QSettings 快照、
删掉探针新建的键，并把窗口几何设成 (0,0,1134,820) 再关。

⚠️ 2026-10-17 修：①~④ 的遍历原先写成 `for key in TRACK`（TRACK 是
(短名, 属性名) 元组），`_series` 拿到元组当键 → 永远是空 → 四条判据
全部静默输出"（无）"。本探针在那之前**没有真正生效过**，别信旧结论。

用法：python devtools/_probe_motion_jumps.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import QPoint, QSettings              # noqa: E402
from PySide6.QtWidgets import QApplication                # noqa: E402

JUMP_PX = 2.0        # 帧间位移超过这个值算"跳"的候选
ISOLATED_PX = 1.0    # 前后邻帧位移小于这个值 → 判为"孤立跳变"（真正的跳）
SYNC_MS = 40.0       # 同步判定：到达时间差超过这个值报警
SETTLE_MS = 350.0    # 多久没有任何变化算落定
MIN_RUN_MS = 1000.0  # 每个过程至少采这么久（要盖住 240ms 的搬家定时器）
MAX_RUN_MS = 3400.0  # 每个过程最多采这么久

# 每个过程要盯的控件（屏幕几何 + 可见性）。
# 左边是报告里用的短名，右边是 MainWindow 上的真实属性名。
TRACK = (("tx_card", "_tx_card"), ("tx_content", "_tx_content"),
         ("txt_tx", "txt_tx"), ("rx_card", "_rx_card"),
         ("txt_rx", "txt_rx"), ("rx_rail", "_rx_rail"),
         ("btn_expand", "btn_expand"), ("open_row", "_open_row"),
         ("btn_open", "btn_open"), ("brand", "brand_header"),
         ("config", "_config_card"), ("rx_opts", "_rx_opts_row"))

RAW_PATH = os.path.join(_ROOT, "_check", "motion_jumps_raw.txt")


# ---------------------------------------------------------------- 采样

def _elements(window):
    return {name: getattr(window, attr) for name, attr in TRACK}


def _grab(window, elements, t_ms):
    row = {"t": round(t_ms, 1)}
    for name, w in elements.items():
        try:
            r = w.geometry()
            p = w.mapToGlobal(QPoint(0, 0))
            row[name] = [p.x(), p.y(), r.width(), r.height(),
                         1 if w.isVisible() else 0]
        except RuntimeError:                    # 窗口收尾竞态，探针里不该有
            row[name] = None
    try:
        row["split"] = list(window._splitter.sizes())
    except RuntimeError:
        row["split"] = None
    parent = window._open_row.parentWidget()
    if parent is window._rx_rail:
        row["open_parent"] = "rail"
    else:
        row["open_parent"] = "config"
    wr = window.geometry()
    row["win"] = [wr.x(), wr.y(), wr.width(), wr.height()]
    row["fs"] = 1 if window.isFullScreen() else 0
    return row


def _signature(row):
    return tuple((k, tuple(v) if isinstance(v, list) else v)
                 for k, v in row.items() if k != "t")


def record(app, window, elements, trigger):
    """起 trigger，然后逐帧记录，直到落定后 SETTLE_MS。"""
    rows = []
    t0 = time.perf_counter()
    trigger()
    prev_sig = None
    last_change = t0
    while True:
        app.processEvents()
        now = time.perf_counter()
        row = _grab(window, elements, (now - t0) * 1000.0)
        rows.append(row)
        sig = _signature(row)
        if sig != prev_sig:
            prev_sig = sig
            last_change = now
        elapsed = (now - t0) * 1000.0
        if elapsed >= MAX_RUN_MS:
            break
        if (elapsed >= MIN_RUN_MS
                and (now - last_change) * 1000.0 >= SETTLE_MS):
            break
        time.sleep(0.001)
    return rows


def compress(rows):
    """相邻重复行只留第一行（落定时刻由最后一行体现）。"""
    out = []
    for row in rows:
        if not out or _signature(out[-1]) != _signature(row):
            out.append(row)
    return out


# ---------------------------------------------------------------- 分析

FIELD = {"x": 1, "y": 2, "w": 3, "h": 4}     # row[name] = [x, y, w, h, vis]
NAME = {"x": "X", "y": "Y", "w": "宽", "h": "高"}


def _series(rows, key):
    out = []
    for r in rows:
        v = r.get(key)
        if v is not None:
            out.append((r["t"], v[0], v[1], v[2], v[3], v[4]))
    return out


def find_jumps(rows, key):
    """帧间位移 > JUMP_PX 的候选；标注是否孤立阶跃。"""
    series = _series(rows, key)
    events = []
    for i in range(1, len(series) - 1):
        for field, idx in FIELD.items():
            a, b = series[i - 1][idx], series[i][idx]
            d = b - a
            if abs(d) <= JUMP_PX:
                continue
            isolated = ((abs(series[i - 2][idx] - a) < ISOLATED_PX)
                        if i >= 2 else True) \
                and abs(series[i + 1][idx] - b) < ISOLATED_PX
            events.append({
                "t_from": series[i - 1][0], "t_to": series[i][0],
                "field": field, "from": a, "to": b, "delta": d,
                "isolated": isolated,
                "gap_ms": round(series[i][0] - series[i - 1][0], 1),
                "ctx": (round(abs(series[i - 2][idx] - a), 1) if i >= 2 else None,
                        round(abs(series[i + 1][idx] - b), 1)),
            })
    return events


def find_nonmonotonic(rows, key):
    """方向自动判（按首尾差）。"""
    series = _series(rows, key)
    if len(series) < 3:
        return []
    events = []
    for field, idx in FIELD.items():
        vals = [s[idx] for s in series]
        total = vals[-1] - vals[0]
        want_down = total < -4
        if not want_down:
            continue
        direction = None
        for i in range(1, len(vals)):
            d = vals[i] - vals[i - 1]
            if abs(d) <= JUMP_PX:
                continue
            cur = d < 0
            if direction is None:
                direction = cur
            elif cur != direction:
                events.append({"t": series[i][0], "field": field,
                               "value": vals[i], "delta": d})
                direction = cur
    return events


def find_flips(rows, key):
    series = _series(rows, key)
    events = []
    for a, b in zip(series, series[1:]):
        if a[5] != b[5]:
            events.append({"t": b[0], "visible": bool(b[5])})
    return events


def arrival(rows, key, field, tol=2.0):
    """第一次落到终值 ±tol 内且此后不离开的时刻（毫秒）。"""
    series = _series(rows, key)
    idx = FIELD[field]
    if len(series) < 2:
        return None
    target = series[-1][idx]
    for i, s in enumerate(series):
        if all(abs(x[idx] - target) <= tol for x in series[i:]):
            return s[0]
    return None


def find_reparent(rows):
    events = []
    prev = None
    for r in rows:
        cur = r.get("open_parent")
        if prev is not None and cur != prev:
            events.append({"t": r["t"], "from": prev, "to": cur})
        prev = cur
    return events


# ---------------------------------------------------------------- 报告

def report(name, rows):
    rows = compress(rows)
    lines = []
    lines.append("=" * 74)
    lines.append(f"过程：{name}")
    lines.append(f"采样 {len(rows)} 个变化段，跨度 {rows[0]['t']:.0f}~{rows[-1]['t']:.0f}ms")
    base = rows[0]
    tail = rows[-1]
    lines.append("起手/落定关键值："
                 f"tx_card.h {base['tx_card'][3]}→{tail['tx_card'][3]}  "
                 f"split {base['split']}→{tail['split']}  "
                 f"rx_card.h {base['rx_card'][3]}→{tail['rx_card'][3]}  "
                 f"config.w {base['config'][2]}→{tail['config'][2]}  "
                 f"brand.h {base['brand'][3]}→{tail['brand'][3]}  "
                 f"rail.w {base['rx_rail'][2]}→{tail['rx_rail'][2]}  "
                 f"win {base['win'][2]}x{base['win'][3]}→{tail['win'][2]}x{tail['win'][3]}")

    # ① 帧间阶跃
    lines.append("-- ① 帧间阶跃（>2px；★ = 前后帧都基本不动的孤立跳变）--")
    any_jump = False
    for key, _attr in TRACK:
        for e in find_jumps(rows, key):
            any_jump = True
            star = "★" if e["isolated"] else " "
            lines.append(
                f" {star} t={e['t_from']:.0f}→{e['t_to']:.0f}ms  "
                f"{key}.{NAME[e['field']]}  {e['from']}→{e['to']}  "
                f"(Δ{e['delta']:+}, 帧隔 {e['gap_ms']}ms, "
                f"前后帧位移 {e['ctx'][0]}/{e['ctx'][1]}px)")
    if not any_jump:
        lines.append(" （无）")

    # ② 非单调
    lines.append("-- ② 非单调（方向翻转 >2px）--")
    any_nm = False
    for key, _attr in TRACK:
        for e in find_nonmonotonic(rows, key):
            any_nm = True
            lines.append(f"   t={e['t']:.0f}ms  {key}.{NAME[e['field']]} "
                         f"折返到 {e['value']}（Δ{e['delta']:+}）")
    if not any_nm:
        lines.append(" （无）")

    # ③ 显隐翻转
    lines.append("-- ③ isVisible 翻转 --")
    any_flip = False
    for key, _attr in TRACK:
        for e in find_flips(rows, key):
            any_flip = True
            lines.append(f"   t={e['t']:.0f}ms  {key} → "
                         f"{'可见' if e['visible'] else '隐藏'}")
    if not any_flip:
        lines.append(" （无）")

    # ④ 同步（有位移的元素的到达时间两两比对）
    lines.append(f"-- ④ 到达时间差（> {SYNC_MS:.0f}ms 报警；到达 = 落到终值±2px）--")
    moved = {}
    for key, _attr in TRACK:
        series = _series(rows, key)
        if len(series) < 2:
            continue
        for field, idx in FIELD.items():
            if abs(series[-1][idx] - series[0][idx]) > 4:
                a = arrival(rows, key, field)
                if a is not None:
                    moved[(key, field)] = a
    # ⚠️ 这里原先是 `for k, v in keys` —— keys 的元素是 (控件, 字段) 二元组，
    #    解包出来的 v 其实是字段名（字符串），拿去 `:.0f` 必崩。改成遍历 items。
    keys = sorted(moved.items(), key=lambda kv: kv[1])
    reported = False
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            diff = keys[j][1] - keys[i][1]
            if diff > SYNC_MS:
                reported = True
                (ka, fa), ta = keys[i]
                (kb, fb), tb = keys[j]
                lines.append(f"   {ka}.{NAME[fa]} {ta:.0f}ms  vs  "
                             f"{kb}.{NAME[fb]} {tb:.0f}ms   差 {diff:.0f}ms")
    if not reported:
        lines.append(" （没有超过阈值的组合）")
    if keys:
        lines.append("   全部到达时间：" + "  ".join(
            f"{k[0]}.{NAME[k[1]]}={t:.0f}ms" for k, t in keys))

    # ⑤ reparent
    lines.append("-- ⑤ 串口开关 reparent --")
    reps = find_reparent(rows)
    if not reps:
        lines.append(" （本过程未搬家）")
    for e in reps:
        idx = next((i for i, r in enumerate(rows) if r["t"] >= e["t"]),
                   len(rows) - 1)
        before = rows[max(0, idx - 1)]
        after = rows[idx]
        for key in ("open_row", "btn_open", "rx_rail", "switch_box",
                    "txt_rx"):
            b, a = before.get(key), after.get(key)
            if b and a:
                lines.append(
                    f"   t={e['t']:.0f}ms {e['from']}→{e['to']}："
                    f"{key} ({b[0]},{b[1]}) {b[2]}x{b[3]} → "
                    f"({a[0]},{a[1]}) {a[2]}x{a[3]}"
                    f"（Δpos=({a[0]-b[0]:+},{a[1]-b[1]:+}) "
                    f"Δsize=({a[2]-b[2]:+},{a[3]-b[3]:+})）")
    lines.append("")
    return lines


def dump_raw(all_rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for name, rows in all_rows:
            f.write(f"===== {name} =====\n")
            for r in compress(rows):
                parts = [f"t={r['t']:7.1f}"]
                for key, _attr in TRACK:
                    v = r.get(key)
                    parts.append(f"{key}="
                                 + ("None" if v is None else
                                    f"({v[0]},{v[1]}) {v[2]}x{v[3]}"
                                    f"{'V' if v[4] else 'H'}"))
                parts.append(f"split={r['split']}")
                parts.append(f"open@{r['open_parent']}")
                parts.append(f"win={r['win'][2]}x{r['win'][3]}"
                             f"{'@FS' if r['fs'] else ''}")
                f.write("  ".join(parts) + "\n")
            f.write("\n")


# ---------------------------------------------------------------- 主流程

def pump(app, seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.004)


def main() -> int:
    import hashlib
    with open(os.path.join(_ROOT, "main_window.py"), "rb") as f:
        mw_md5 = hashlib.md5(f.read()).hexdigest()[:12]
    print(f"被测版本：main_window.py md5={mw_md5}")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    from ui_anim import set_animations_enabled_override
    set_animations_enabled_override(True)     # 系统若关了动画，这里强制开

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    import customization
    from ui_theme import apply_scheme, ui_font
    from main_window import MainWindow

    # 探针要还原用户配置：先把定制项重置成默认（默认 = 启动全屏 + 发送区折叠，
    # 这正是用户反馈时的默认场景）
    for spec in customization.specs():
        customization.set(spec["key"], spec["default"])
    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    window = MainWindow()
    window._port_watch.stop()
    window.show()
    window.raise_()
    window.apply_startup_state()      # 整窗全屏，和真实启动一致
    pump(app, 1.5)                    # 落定

    elements = _elements(window)
    results = []

    def run(name, trigger):
        rows = record(app, window, elements, trigger)
        results.append((name, rows))
        for line in report(name, rows):
            print(line)
        pump(app, 0.6)                # 过程间留空隙，别把上一段的尾巴带进来

    try:
        # -- 热身：把各 Reveal / 补间都建出来，首次构造开销不计入测量 --
        window._expand_receive()
        pump(app, 1.2)
        window._collapse_receive()
        pump(app, 1.2)
        window._set_send_collapsed(False, remember=False)   # 展开发送区
        pump(app, 0.8)
        window._set_send_collapsed(True)                    # 再收起
        pump(app, 0.8)

        # P1 发送区展开（当前折叠 → 展开）
        run("P1 发送区展开（手动，300ms 分割器动画）",
            lambda: window._set_send_collapsed(False))

        # P2 发送区折叠（当前展开 → 折叠）
        run("P2 发送区折叠（手动，300ms 分割器动画）",
            lambda: window._set_send_collapsed(True))

        # P3/P4 接收区最大化/退出 —— 发送区展开态
        window._set_send_collapsed(False, remember=False)
        pump(app, 0.8)
        run("P3 接收区最大化（发送区=展开态，_maximize_shrink_send）",
            window._expand_receive)
        run("P4 退出最大化（发送区=展开态）", window._collapse_receive)

        # P5/P6 接收区最大化/退出 —— 发送区折叠态（默认开机场景）
        window._set_send_collapsed(True)
        pump(app, 0.8)
        run("P5 接收区最大化（发送区=折叠态，默认场景）",
            window._expand_receive)
        run("P6 退出最大化（发送区=折叠态，默认场景）",
            window._collapse_receive)

        # P7/P8 普通窗口下的最大化/退出（_pre_expand_fullscreen=False 那一支）
        window._on_escape()            # 先退出窗口全屏 → 普通窗口
        pump(app, 1.0)
        run("P7 接收区最大化（普通窗口，窗口随动画放大）",
            window._expand_receive)
        run("P8 退出最大化（普通窗口，showNormal+还原几何）",
            window._collapse_receive)
    finally:
        dump_raw(results, RAW_PATH)
        print(f"\n原始逐帧数据 → {os.path.relpath(RAW_PATH, _ROOT)}")
        # 收尾：先给一个正常几何再关（全屏状态下直接关会留下全屏矩形），
        # 然后还原 QSettings 快照并删掉探针新建的键
        try:
            window.setGeometry(0, 0, 1134, 820)
            window.close()
        except RuntimeError:
            pass
        for key in list(settings.allKeys()):
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            if value is not None:
                settings.setValue(key, value)
        settings.sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
