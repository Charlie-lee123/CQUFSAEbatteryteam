# -*- coding: utf-8 -*-
r"""B2 自验探针：证明「淡出开始前」回调在 ``finish()`` 内、淡出 timer 启动之前触发。

⛔ 不启动完整开屏、不调 ``play_configured()`` / ``main()``、不写 QSettings、
   offscreen + 240×160 小窗 + 短命；只用于证明**调用时序**，不证明前台是否
   真的抢到（那部分由 team-lead 的
   ``devtools/_probe_b2_early_activate.py`` 真机 GetForegroundWindow A/B 证明）。

检查项
------
S1 产品源码里 ``set_on_fade_start`` 恰好 1 定义 + 1 调用点
S2 ``fade_cb()`` 链路在 ``_fade_timer`` 创建之前
S3 ``_on_splash_done`` 的 raise_()/activateWindow() 兜底仍在，且新回调没有
   复制 ``_skip_startup_anim`` / ``_fs_idle_pix`` / ``singleShot(200`` 三件事
T1 回调恰好触发 1 次，且触发时 ``_done is True``（= 在 finish() 内部）
T2 回调触发时 ``_fade_timer is None`` 且 ``_fade_elapsed is None``
   （= 在淡出 timer / 墙钟创建之前）
T3 ``finish()`` 返回后 ``_fade_timer`` 已创建并 start，间隔与 frame_hz() 同式
T4 回调时刻 < ``_fade_step`` 首拍时刻
T5 ``set_on_fade_start`` 的 ``_done`` 即时补调分支仍有效
Q  QSettings 只读快照前后一致（零写入）

用法::

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe devtools/_probe_b2_fade_start_timing.py

产物：``_check/b2_fade_start_timing.txt``
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRect, QSettings, QTimer   # noqa: E402
from PySide6.QtWidgets import QApplication           # noqa: E402

REPORT: list[str] = []


def say(line: str = "") -> None:
    print(line)
    REPORT.append(line)


def _settings_snapshot() -> dict:
    s = QSettings("MimoWorks", "SerialAssistant")
    return {k: s.value(k) for k in s.allKeys()}


def _statics(main_src: str, splash_src: str) -> bool:
    say("=" * 78)
    say("S 静态核对")
    say("=" * 78)
    main_lines = main_src.splitlines()
    splash_lines = splash_src.splitlines()

    def find_line(lines: list[str], needle: str) -> int:
        for i, ln in enumerate(lines, start=1):
            if needle in ln:
                return i
        return -1

    defs = sum(1 for ln in main_lines + splash_lines
               if re.match(r"\s*def set_on_fade_start\(", ln))
    calls = sum(1 for ln in main_lines
               if "_splash.set_on_fade_start(_on_fade_start)" in ln)
    # 探针自身也调用了一次，但那是 devtools 测试代码，不算产品调用点
    says = [
        ("set_on_fade_start 产品源码 1 定义", defs == 1, f"实测 {defs}"),
        ("set_on_fade_start 产品源码 1 调用点", calls == 1, f"实测 {calls}"),
        ("挂点紧跟 set_on_finished（≤4 行内）",
         0 < (find_line(main_lines, "_splash.set_on_fade_start(_on_fade_start)")
              - find_line(main_lines, "_splash.set_on_finished(_on_splash_done)")) <= 4,
         f"set_on_finished 行 {find_line(main_lines, '_splash.set_on_finished(_on_splash_done)')}"),
        ("fade_cb() 在 _fade_timer 创建之前",
         0 < find_line(splash_lines, "fade_cb()")
         < find_line(splash_lines, "self._fade_timer = QTimer(self)"),
         f"fade_cb 行 {find_line(splash_lines, 'fade_cb()')} / "
         f"timer 行 {find_line(splash_lines, 'self._fade_timer = QTimer(self)')}"),
        ("_on_splash_done 的 raise_()/activateWindow() 兜底仍在",
         "window.raise_()" in main_src and "window.activateWindow()" in main_src
         and main_src.count("window.raise_()") >= 2,
         f"raise_ 出现 {main_src.count('window.raise_()')} 次"),
        ("新回调未复制 _skip_startup_anim / _fs_idle_pix / singleShot(200",
         not _copied_things(main_src), "逐字检查 _on_fade_start 函数体"),
        ("_on_splash_done 三件事仍在原处",
         "window._skip_startup_anim = False" in main_src
         and "window._fs_idle_pix = None" in main_src
         and "QTimer.singleShot(200, window._build_fs_idle_pix)" in main_src,
         ""),
        ("日志用单参数 f-string（app_log.warning 只收 1 个位置参数）",
         'app_log.warning(f"开屏淡出前主窗置前失败：{exc!r}")' in main_src, ""),
        ("finish() 逻辑未被改（无回调日志接线混入）",
         splash_src.count("fade_cb, self._on_fade_start") == 1
         and "开屏淡出前主窗置前失败" not in splash_src, ""),
        ("app_log 是 main() 局部 import 且早于新回调（闭包可见）",
         (lambda m, f: 0 < m < f and any(
             "    import app_log" in ln
             for ln in main_lines[m:f]))(
                 find_line(main_lines, "def main() -> int:"),
                 find_line(main_lines, "    def _on_fade_start() -> None:")),
         f"main() 行 {find_line(main_lines, 'def main() -> int:')} / "
         f"回调行 {find_line(main_lines, '    def _on_fade_start() -> None:')}"),
    ]
    for name, ok, extra in says:
        say(f"  {'✓' if ok else '✗'} {name}" + (f"  [{extra}]" if extra else ""))
    return all(ok for _n, ok, _e in says)


def _copied_things(main_src: str) -> bool:
    """新回调是否误复制了 _on_splash_done 的三件事（True = 误复制）。"""
    start = main_src.find("    def _on_fade_start() -> None:")
    if start < 0:
        return True
    end = main_src.find("\n    if _splash is not None:", start)
    body = main_src[start:end if end > 0 else None]
    forbidden = ("_skip_startup_anim", "_fs_idle_pix",
                 "singleShot(200", "_build_fs_idle_pix")
    return any(word in body for word in forbidden)


def _timing(app: QApplication, preset: dict) -> bool:
    import splash_anim
    import ui_anim

    say()
    say("=" * 78)
    say("T 轻量时序探针（offscreen / 240×160 / 不启完整开屏）")
    say("=" * 78)
    w = splash_anim._SplashWindow(preset)
    w.setGeometry(QRect(20, 20, 240, 160))
    w.show()
    app.processEvents()

    t0 = time.perf_counter()
    calls: list[dict] = []
    first_tick: list[float] = []

    def on_fade_start() -> None:
        calls.append({
            "t": time.perf_counter() - t0,
            "done": bool(w._done),
            "timer_none": w._fade_timer is None,
            "elapsed_none": w._fade_elapsed is None,
            "fade_k": float(w._fade_k),
        })

    w.set_on_fade_start(on_fade_start)
    orig_step = w._fade_step

    def step_spy() -> None:
        # ⛔ 只取首拍就停表：offscreen 下 4ms 定时器可能一路跑到 k=1 触发
        #    _close() → WA_DeleteOnClose 删掉 QTimer 的 C++ 对象，事后读
        #    interval() 会抛 RuntimeError（首跑踩过）。
        if not first_tick:
            first_tick.append(time.perf_counter() - t0)
            ft = w._fade_timer
            if ft is not None:
                ft.stop()
        orig_step()

    w._fade_step = step_spy

    w.finish()
    t_return = time.perf_counter() - t0
    # 所有 QTimer/QElapsedTimer 读数在事件循环之前固化成 Python 值
    timer = w._fade_timer
    interval_now = int(timer.interval()) if timer is not None else -1
    active_now = bool(timer.isActive()) if timer is not None else False
    elapsed_ready = w._fade_elapsed is not None
    timer = None                       # 丢弃引用，避免 C++ 对象删除后误用
    hz = max(60, int(ui_anim.frame_hz()))
    want = max(1, int(round(1000.0 / hz)) - 4)

    QTimer.singleShot(80, app.quit)
    app.exec()

    c = calls[0] if calls else {}
    say(f"  frame_hz()={hz} → 期望淡出间隔 {want}ms，实测 {interval_now}ms")
    checks = [
        ("T1 回调恰好 1 次", len(calls) == 1, f"实测 {len(calls)}"),
        ("T1 回调时 _done=True（在 finish() 内部）",
         c.get("done") is True, f"实测 {c.get('done')}"),
        ("T1 回调时刻 < finish() 返回",
         c.get("t", 9e9) < t_return,
         f"t_cb={c.get('t', -1) * 1000:.3f}ms  t_return={t_return * 1000:.3f}ms"),
        ("T2 回调时 _fade_timer is None",
         c.get("timer_none") is True, f"实测 {c.get('timer_none')}"),
        ("T2 回调时 _fade_elapsed is None",
         c.get("elapsed_none") is True, f"实测 {c.get('elapsed_none')}"),
        ("T2 回调时 _fade_k 仍为 0（开屏未开始淡）",
         c.get("fade_k") == 0.0, f"实测 {c.get('fade_k')}"),
        ("T3 finish() 后 _fade_timer 已创建且 active",
         active_now and interval_now >= 0, f"active={active_now}"),
        ("T3 间隔与 frame_hz() 同式",
         interval_now == want, f"{interval_now} vs {want}"),
        ("T3 finish() 后 _fade_elapsed 已就绪", elapsed_ready, ""),
        ("T4 拿到 _fade_step 首拍", len(first_tick) == 1,
         f"实测 {len(first_tick)}"),
        ("T4 回调时刻 < 首拍时刻",
         bool(first_tick) and c.get("t", 9e9) < first_tick[0],
         f"t_cb={(c.get('t', -1)) * 1000:.3f}ms  "
         f"t_tick={(first_tick[0] if first_tick else -1) * 1000:.3f}ms"),
    ]
    for name, ok, extra in checks:
        say(f"  {'✓' if ok else '✗'} {name}" + (f"  [{extra}]" if extra else ""))

    # T5：_done 即时补调分支（开屏已 finish 才注册，也应立刻触发）
    w2 = splash_anim._SplashWindow(preset)
    w2.setGeometry(QRect(20, 20, 240, 160))
    w2.show()
    app.processEvents()
    w2.finish()
    late: list[int] = []
    w2.set_on_fade_start(lambda: late.append(1))
    t5 = len(late) == 1
    say(f"  {'✓' if t5 else '✗'} T5 _done 即时补调分支  [触发 {len(late)} 次]")
    if w2._fade_timer is not None:
        w2._fade_timer.stop()
    w2.hide()
    app.processEvents()
    return all(ok for _n, ok, _e in checks) and t5


def main() -> int:
    app = QApplication(sys.argv)
    snap_before = _settings_snapshot()
    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    splash_src = (ROOT / "splash_anim.py").read_text(encoding="utf-8")
    preset = {"id": "preset:b2probe", "label": "b2probe",
              "path": str(ROOT / "assets" / "splash"
                          / "splash_dark_planb_4k60.mp4"),
              "kind": "video", "theme": "dark"}
    say(f"平台 = {os.environ.get('QT_QPA_PLATFORM')}（headless，仅证调用时序）")
    say(f"main.py md5       = {hashlib.md5((ROOT / 'main.py').read_bytes()).hexdigest()}")
    say(f"splash_anim.py md5= "
        f"{hashlib.md5((ROOT / 'splash_anim.py').read_bytes()).hexdigest()}")

    s_ok = _statics(main_src, splash_src)
    t_ok = _timing(app, preset)
    snap_after = _settings_snapshot()
    q_ok = snap_after == snap_before
    say()
    say(f"Q  QSettings 零污染（只读比对，未 setValue/sync）: "
        f"{'PASS' if q_ok else 'FAIL'}  keys {len(snap_before)} → {len(snap_after)}")

    ok = s_ok and t_ok and q_ok
    say()
    say("=" * 78)
    say(f"总判定：{'PASS' if ok else 'FAIL'}   S={s_ok} T={t_ok} Q={q_ok}")
    say("=" * 78)
    say("⚠️ 本探针只证明『回调在 finish() 内、淡出 timer 启动之前、首拍之前』。")
    say("   前台是否真的抢到 → team-lead 真机 A/B（_probe_b2_early_activate.py）；")
    say("   B2 是否彻底消失 → 打包后新旧客观 A/B 录屏判决（未判）。")
    out = ROOT / "_check" / "b2_fade_start_timing.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(REPORT) + "\n", encoding="utf-8")
    print(f"\n报告已写 {out.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
