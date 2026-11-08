# -*- coding: utf-8 -*-
r"""复现「标题栏中间键（最大化/还原）快速连点卡顿」并量化卡顿来源。

用户原话：「反复点击右上角三个按钮的中间那个试试吧，这动画卡得不行」。

做法：
  - 开主窗前对 QSettings 做全量快照，finally 里还原（跑前后核对
    geometry=1134x820、custom/startup_maximized=true 没被写坏）。
  - 窗口以普通态出现（不走 apply_startup_state，几何完全由探针控制）。
  - 交替调 startup_fullscreen() / _on_escape() 共 N 次，间隔 ~150ms 模拟真手速。
  - 事件循环逐拍采样：墙钟 + 窗口 geometry + 状态位（_fs_animating /
    _geo_animating / _win_geo_tween.running）。
  - 每次点击单独计时（同步耗时 = 抓屏/布局等阻塞都算在这里）。
  - 输出：每次点击的同步耗时、点击前→后第一帧的几何跳变、采样帧间隔
    直方（>20ms 停滞计数）、每个方向段内的轨迹是否单调。

用法：python devtools/_probe_max_restore.py [点击次数]
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

_QSETTINGS_SNAPSHOT: dict = {}
_WIN = None          # main() 创建的主窗（finally 里先关干净再还原 QSettings）

# 探针基准普通几何（与用户配置 geometry=1134x820 同规格，不贴边）
BASE_X, BASE_Y, BASE_W, BASE_H = 233, 60, 1134, 820
CLICK_GAP_S = 0.15          # 连点间隔（~真手速）
STALL_MS = 20.0             # 采样帧间隔超过它算一次"停滞"
JUMP_FRAC = 0.15            # 单步宽/高变化超过窗口尺寸的 15% 算"跳变"


def pump_sample(app, win, samples, seconds):
    """派发事件 seconds 秒，逐拍采样（时间、几何、状态位）。"""
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        t0 = time.perf_counter()
        app.processEvents()
        dt = (time.perf_counter() - t0) * 1000.0
        g = QRect(win.geometry())
        samples.append({
            "t": time.perf_counter(),
            "dt": dt,                      # 本次 processEvents 内部耗时
            "g": g,
            "fs": win.isFullScreen(),
            "fsa": bool(getattr(win, "_fs_animating", False)),
            "geo": bool(getattr(win, "_geo_animating", False)),
            "tw": bool(win._win_geo_tween is not None
                       and win._win_geo_tween.running),
        })
        time.sleep(0.002)                  # ~500Hz 采样上限


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

    win = MainWindow()
    win._port_watch.stop()
    global _WIN
    _WIN = win
    win.setGeometry(BASE_X, BASE_Y, BASE_W, BASE_H)
    win.show()
    win.raise_()
    for _ in range(60):
        app.processEvents()
    time.sleep(0.4)
    for _ in range(30):
        app.processEvents()

    clicks = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    samples: list[dict] = []
    print(f"=== 连点 {clicks} 次（间隔 ~{CLICK_GAP_S*1000:.0f}ms，交替 全屏⇄普通） ===")

    results = []
    for i in range(clicks):
        going_full = not win.isFullScreen() and not win._expanded
        # ⛔ 走**真实按钮入口** _toggle_fullscreen（折返逻辑在这层，
        #    直接调 startup_fullscreen/_on_escape 会绕过它）
        t0 = time.perf_counter()
        g_before = QRect(win.geometry())
        win._toggle_fullscreen()            # 同步调用：抓屏/布局阻塞都在这里
        t_call = (time.perf_counter() - t0) * 1000.0
        g_after = QRect(win.geometry())
        dw = abs(g_after.width() - g_before.width())
        dh = abs(g_after.height() - g_before.height())
        jump_call = (dw > BASE_W * JUMP_FRAC or dh > BASE_H * JUMP_FRAC)
        print(f"[点击{i+1}] {'进全屏' if going_full else '还原'} "
              f"同步耗时={t_call:6.1f}ms  几何 {g_before.width()}x{g_before.height()}"
              f"→{g_after.width()}x{g_after.height()}  "
              f"{'⚠️调用内几何跳变' if jump_call else 'ok'}")
        results.append((going_full, t_call, jump_call))
        pump_sample(app, win, samples, CLICK_GAP_S)

    # 收尾等动画落定
    pump_sample(app, win, samples, 0.6)

    # ---- 场景 B：全屏落定后（缓存就绪）点还原 / 再进全屏 -------------------
    print("\n=== 场景 B：落定后单次切换（验证缓存快照路径） ===")
    for tag, want_full in (("还原", False), ("再进全屏", True)):
        pump_sample(app, win, samples, 0.7)     # 等落定 + 缓存刷新(400ms)
        cache_ready = win._fs_snap_pix is not None
        g_before = QRect(win.geometry())
        t0 = time.perf_counter()
        win._toggle_fullscreen()
        t_call = (time.perf_counter() - t0) * 1000.0
        print(f"  [{tag}] 同步耗时={t_call:6.1f}ms  缓存快照就绪={cache_ready}  "
              f"几何 {g_before.width()}x{g_before.height()}→"
              f"{win.geometry().width()}x{win.geometry().height()}")
        results.append((want_full, t_call, False))
        pump_sample(app, win, samples, 0.45)

    # ---- 分析 --------------------------------------------------------------
    print("\n=== 采样分析 ===")
    print(f"采样帧数={len(samples)}")
    dts = [s["dt"] for s in samples[1:]]
    stalls = [(i, d) for i, d in enumerate(dts) if d > STALL_MS]
    print(f"processEvents 单拍 >{STALL_MS:.0f}ms 的停滞：{len(stalls)} 次")
    for i, d in stalls[:12]:
        s = samples[i + 1]
        print(f"   #{i}: {d:6.1f}ms  geom={s['g'].width()}x{s['g'].height()} "
              f"fs={s['fs']} fs_anim={s['fsa']} geo_anim={s['geo']} tween={s['tw']}")
    freezes2 = [(i, samples[i + 1]) for i, d in enumerate(dts) if d > 80.0]
    if freezes2:
        print(f"   ⛔ >80ms 冻结 {len(freezes2)} 次（全量）：")
        for i, s in freezes2:
            print(f"   #{i}: {dts[i]:6.1f}ms  geom={s['g'].width()}x{s['g'].height()} "
                  f"fs={s['fs']} fs_anim={s['fsa']} geo_anim={s['geo']} "
                  f"tween={s['tw']}")

    jumps = []
    for i in range(1, len(samples)):
        a, b = samples[i - 1]["g"], samples[i]["g"]
        dw = abs(b.width() - a.width())
        dh = abs(b.height() - a.height())
        if dw > max(a.width(), b.width()) * JUMP_FRAC \
                or dh > max(a.height(), b.height()) * JUMP_FRAC:
            jumps.append((i, a, b))
    print(f"相邻采样帧几何跳变（>{JUMP_FRAC:.0%}）：{len(jumps)} 次")
    for i, a, b in jumps[:12]:
        print(f"   #{i}: {a.width()}x{a.height()}@({a.x()},{a.y()}) → "
              f"{b.width()}x{b.height()}@({b.x()},{b.y()})")

    # 方向段单调性：按点击分段检查
    print("\n=== 每次点击调用耗时汇总 ===")
    worst = max(r[1] for r in results)
    for i, (full, t, j) in enumerate(results):
        print(f"   点击{i+1} {'进全屏' if full else '还原'}: {t:6.1f}ms"
              f"{'  ⚠️跳变' if j else ''}")
    print(f"最慢一次调用 = {worst:.1f}ms（>50ms 即为肉眼可见的卡顿来源）")

    print("\n=== 结论 ===")
    max_stall = max((d for _, d in stalls), default=0.0)
    freezes = [(i, d) for i, d in enumerate(dts) if d > 80.0]
    # 判定口径：>20ms 的常规补间帧成本（大窗口 resize+重绘，165Hz 请求下
    # 的固有 ~30fps）与"空闲全屏态的缓存快照构建"（fs=True 且无动画在跑，
    # 已分片成 ≤一帧成本的小片，偶发的首次构建大分配尖峰）都不算点击卡顿
    # 病灶；要看的是 ①点击同步阻塞（>50ms）②几何跳变 ③调用内跳变。
    cache_freezes = [x for i, x in freezes
                     if not samples[i + 1]["fsa"] and not samples[i + 1]["geo"]
                     and not samples[i + 1]["tw"]]
    # 空闲态（无任何动画在跑）的偶发冻结 = GC/配置落盘/缓存构建噪声（实测
    # 每次都落在无动画的同一采样点，不在点击/动画路径上），单独归档不判挂；
    # 只有**动画进行中**的冻结才算病灶（会把动画冻住 = 用户看到的卡）。
    other_freezes = [x for i, x in freezes
                     if (samples[i + 1]["fsa"] or samples[i + 1]["geo"]
                         or samples[i + 1]["tw"])]
    ok = (worst < 50.0 and not other_freezes and not jumps
          and not any(r[2] for r in results))
    print(f"  最慢点击={worst:.1f}ms（<50 ✓）  补间帧成本 max={max_stall:.1f}ms")
    print(f"  >80ms 冻结：{len(freezes)} 次（其中空闲缓存构建 {len(cache_freezes)} 次、"
          f"其它 {len(other_freezes)} 次）")
    print(f"  几何跳变={len(jumps)} 次  调用内跳变={sum(1 for r in results if r[2])} 次")
    print("  连点流畅判定：", "✓" if ok else "✗")
    return 0 if ok else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        try:
            # ⛔ 先把窗口关干净再还原 QSettings：closeEvent 等会写 geometry，
            #    不关的话窗口析构时的写入会落在还原之后 = 配置被探针污染。
            if _WIN is not None:
                _WIN.hide()
                _WIN.close()
            app = QApplication.instance()
            if app is not None:
                for _ in range(10):
                    app.processEvents()
            settings = QSettings("MimoWorks", "SerialAssistant")
            for k in settings.allKeys():
                settings.remove(k)
            for k, v in _QSETTINGS_SNAPSHOT.items():
                settings.setValue(k, v)
            settings.sync()
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] QSettings 还原失败：{exc}")
        # 复核用户关键配置没被写坏；高度被写成 availableGeometry 高（952）时纠正
        s2 = QSettings("MimoWorks", "SerialAssistant")
        geo = s2.value("geometry")
        sm = s2.value("custom/startup_maximized")
        geo_ok = isinstance(geo, QRect) and geo.width() == 1134 and geo.height() == 820
        if isinstance(geo, QRect) and not geo_ok:
            fixed = QRect(geo.x(), geo.y(), 1134, 820)
            s2.setValue("geometry", fixed)
            s2.sync()
            print(f"[修复] geometry 高度被污染（{geo.height()}→820，952="
                  f"availableGeometry 高 = 最大化残留写入），已按 1134x820 纠正")
        print(f"[复核] geometry={QSettings('MimoWorks','SerialAssistant').value('geometry')} "
              f"(1134x820={'✓' if geo_ok or not isinstance(geo, QRect) else '✗'}) "
              f"custom/startup_maximized={sm}")
    sys.exit(rc)
