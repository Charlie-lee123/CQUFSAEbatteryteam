# -*- coding: utf-8 -*-
r"""最大化（进全屏）动画录屏取证 v2（2026-11-04 od19 验收）。

背景：用户报 od18「最大化的动画会闪」。od19 改成「覆盖层先铺好 → 延迟 100ms
等 DWM 合成 → 才 showFullScreen + 藏主窗 + 覆盖层缩放」。

做法（与 _record_restore_v2.py 同套路，那边验证过可用）：
  - 黑底垫底窗（置顶 + 透明输入）+ 主窗置顶 —— 否则会被 WorkBuddy 全屏窗抢镜。
  - 时间线：show 1134x820 → 1200ms startup_fullscreen() → 3000ms _on_escape()
    （顺带录还原方向，不影响本任务的判定）→ 4500ms 退出。
  - gdigrab 30fps 3200x2000 录 10s，stdin "q" 优雅退出（terminate 会丢 moov）。
  - ⛔ 进程内 30ms 采样 geometry/opacity/cover 状态 + 采样间隔：gdigrab 实际
    ~14fps 且 app 启动耗时波动大，只用 drop_idx 猜帧号会得出假结论。
  - 逐帧：非黑(>40) 包围盒 = 窗口矩形；另算包围盒内**内容占比**（空白窗也是
    一个包围盒，必须看有没有内容）。
  - QSettings 全量快照 + 结束还原（新键也删）；线程看门狗 os._exit 兜底。

用法：.venv/Scripts/python.exe devtools/_record_maximize_v2.py
"""
import os
import subprocess
import sys
import threading
import time

_T0 = time.perf_counter()


def _ms() -> float:
    return (time.perf_counter() - _T0) * 1000.0


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtCore import QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_SNAPSHOT: dict = {}
OUT = os.path.join(_ROOT, "_check", "maximize_frames_v2")
os.makedirs(OUT, exist_ok=True)

# 进程内记录（供与视频帧互相印证）
_SAMPLES: list[dict] = []
_EVENTS: list[tuple[float, str]] = []


def _log_event(tag: str) -> None:
    _EVENTS.append((_ms(), tag))
    print(f"EVENT {_ms():7.1f} {tag}", flush=True)


def run_app() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(40, exit=True)
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    app = QApplication.instance() or QApplication(sys.argv)
    import ui_anim
    from main_window import MainWindow

    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    print("STAGE: imports done", flush=True)
    scr = app.primaryScreen()
    sg = scr.geometry()

    backdrop = QWidget(None, Qt.Window | Qt.FramelessWindowHint
                       | Qt.WindowStaysOnTopHint)
    backdrop.setAttribute(Qt.WA_ShowWithoutActivating)
    backdrop.setAutoFillBackground(True)
    pal = backdrop.palette()
    pal.setColor(backdrop.backgroundRole(), QColor(0, 0, 0))
    backdrop.setPalette(pal)
    backdrop.setWindowFlag(Qt.WindowTransparentForInput, True)
    backdrop.setGeometry(sg)
    backdrop.show()
    backdrop.raise_()

    window = MainWindow()
    window._port_watch.stop()
    w, h = 1134, 820
    window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    window.setGeometry(sg.x() + (sg.width() - w) // 2,
                       sg.y() + max(60, (sg.height() - h) // 3), w, h)
    window.show()
    window.raise_()
    window.activateWindow()
    print("STAGE: window shown", flush=True)

    # 观测：空闲快照抓屏的真实耗时（(d) 项：它落在动画之后还是之中）
    _orig_build = MainWindow._build_fs_idle_pix

    def _wrap_build(self):
        t0 = time.perf_counter()
        _log_event("_build_fs_idle_pix 开始")
        _orig_build(self)
        dt = (time.perf_counter() - t0) * 1000.0
        p = self._fs_idle_pix
        desc = "null" if p is None or p.isNull() else f"{p.width()}x{p.height()}"
        _log_event(f"_build_fs_idle_pix 结束 耗时={dt:.0f}ms pix={desc}")

    MainWindow._build_fs_idle_pix = _wrap_build

    # 进程内 30ms 采样：窗口/覆盖层状态 + 事件循环间隔（冻结测得出）
    _sampler = QTimer()
    _sampler.setInterval(30)

    def _sample_state():
        now = _ms()
        s = {"t": now, "dt": (now - _SAMPLES[-1]["t"]) if _SAMPLES else 0.0}
        try:
            g = window.geometry()
            s.update(w=(g.x(), g.y(), g.width(), g.height()),
                     op=window.windowOpacity(), fs=window.isFullScreen())
        except RuntimeError:
            s.update(w=None, op=-1.0, fs=False)
        c = window._cover_label
        if c is not None:
            try:
                cg = c.geometry()
                s.update(cov=(cg.x(), cg.y(), cg.width(), cg.height()),
                         cov_vis=c.isVisible())
            except RuntimeError:
                s.update(cov=None, cov_vis=False)
        else:
            s.update(cov=None, cov_vis=False)
        _SAMPLES.append(s)
        _log_event(f"SAMPLE win={s['w']} op={s['op']:.2f} fs={s['fs']} "
                   f"cover={s['cov']} vis={s['cov_vis']}")

    _sampler.timeout.connect(_sample_state)
    _sampler.start()

    def _max():
        _log_event("startup_fullscreen() 调用")
        window.startup_fullscreen()
        _log_event("startup_fullscreen() 返回")

    def _restore():
        _log_event("_on_escape() 调用")
        window._on_escape()
        _log_event("_on_escape() 返回")

    def _quit():
        _log_event("quit")
        # 采样/事件落盘：分析在**父进程**做，内存数据跨不过去。
        try:
            import json
            with open(os.path.join(OUT, "app_state.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"samples": _SAMPLES, "events": _EVENTS}, fh)
            print("STAGE: app_state.json saved", flush=True)
        except Exception as exc:      # noqa: BLE001
            print(f"STAGE: app_state.json 失败 {exc}", flush=True)
        st2 = QSettings("MimoWorks", "SerialAssistant")
        for k in set(st2.allKeys()) - set(_SNAPSHOT):
            st2.remove(k)
        for k, v in _SNAPSHOT.items():
            st2.setValue(k, v)
        st2.sync()
        print("STAGE: settings restored", flush=True)
        # ⛔ 该探针多次出现 app.quit() 后 exec 不返回（退出竞态，与被测动画
        #    无关）。配置已还原，直接硬退 —— 父进程负责停 ffmpeg + 逐帧分析。
        sys.stdout.flush()
        os._exit(0)

    # ⚠️ 看门狗：即使 _quit 没跑到也硬退（QSettings 还原可能未执行 → 父进程
    #    之后按快照再兜底恢复一次）。
    _wd = threading.Timer(20.0, lambda: os._exit(3))
    _wd.daemon = True
    _wd.start()

    QTimer.singleShot(1200, _max)
    QTimer.singleShot(3200, _restore)
    QTimer.singleShot(5000, _quit)
    print("STAGE: timers scheduled", flush=True)
    app.exec()
    _wd.cancel()
    print("STAGE: exec returned", flush=True)
    return 0


def record() -> str:
    rec = os.path.join(OUT, "maximize_v2.mp4")
    if os.path.exists(rec):
        os.remove(rec)
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "gdigrab", "-framerate", "30",
         "-offset_x", "0", "-offset_y", "0",
         "-video_size", "3200x2000", "-i", "desktop", "-t", "12",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
         rec],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    # ⛔ app 跑在**子进程**里：它退出时 os._exit（该探针 exec 常不返回），
    #    父进程不能被它带走 —— ffmpeg 收尾与逐帧分析都在父进程做。
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    app = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--app"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           env=env)
    try:
        out, _ = app.communicate(timeout=30)
        for line in out.decode("utf-8", "replace").splitlines():
            print(line)
    except subprocess.TimeoutExpired:
        app.kill()
        print("⛔ app 子进程超时被杀")
    time.sleep(2.0)
    try:
        ff.stdin.write(b"q")
        ff.stdin.close()
    except Exception:                 # noqa: BLE001
        pass
    try:
        ff.wait(timeout=15)
    except subprocess.TimeoutExpired:
        ff.terminate()
    print("app rc:", app.returncode)
    return rec


def _frames(rec: str):
    """读全部帧 → (bbox, 内容占比, 帧时间戳ms)。"""
    cap = cv2.VideoCapture(rec)
    rows = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        ts = cap.get(cv2.CAP_PROP_POS_MSEC)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        ys, xs = np.where(gray > 40)
        if len(xs) == 0:
            rows.append(((-1, -1, 0, 0), 0.0, ts))
            continue
        x0, y0 = int(xs.min()), int(ys.min())
        bbox = (x0, y0, int(xs.max() - x0 + 1), int(ys.max() - y0 + 1))
        sub = gray[y0:y0 + bbox[3], x0:x0 + bbox[2]]
        ratio = float((sub > 40).mean())
        rows.append((bbox, ratio, ts))
    cap.release()
    return rows


def analyze(rec: str) -> int:
    # 子进程的采样/事件在 app_state.json 里（跨进程）：读回来做时间轴对齐。
    import json
    _sp = os.path.join(OUT, "app_state.json")
    if os.path.exists(_sp):
        try:
            with open(_sp, encoding="utf-8") as fh:
                data = json.load(fh)
            _SAMPLES[:] = data.get("samples", [])
            _EVENTS[:] = [(float(t), str(tag)) for t, tag in data.get("events", [])]
        except Exception as exc:      # noqa: BLE001
            print(f"[warn] app_state.json 读取失败：{exc}")
    rows = _frames(rec)
    n = len(rows)
    print(f"\n=== 视频：{n} 帧 ===")
    W = 3200
    print("前 12 帧概况：", [(r[0][2], round(r[1] * 100, 1)) for r in rows[:12]])
    # ⛔ 录制开始 1.5s 后才起 app：开头是桌面/WorkBuddy（也满屏），不能拿
    #    "full bbox" 当进全屏标志。正确锚点：
    #    ① 先找第一个"普通窗口宽"帧（≈2268 = 1134 逻辑 × dpr2，黑底垫底上只剩
    #       我们的窗口）→ app 已上屏；
    #    ② 它之后第一个满屏宽帧 = 进全屏完成；
    #    ③ 从它回溯到最后一个普通宽帧 = 增长段起点。
    norm_first = next((i for i, r in enumerate(rows)
                       if 0 < r[0][2] <= 2400), None)
    full_idx = None
    if norm_first is not None:
        full_idx = next((i for i in range(norm_first, n)
                         if rows[i][0][2] >= W - 8), None)
    print(f"app 上屏（普通宽帧）= f{norm_first}；其后首个满屏帧 = f{full_idx}")
    start_idx = None
    if norm_first is not None and full_idx is not None:
        for i in range(full_idx, norm_first - 1, -1):
            if 0 < rows[i][0][2] <= 2400:
                start_idx = i
                break
    print(f"增长段起点 = f{start_idx}")

    ok = True
    if start_idx is None or full_idx is None:
        print("⛔ 没找到进全屏段")
        ok = False
        grow = []
    else:
        grow = rows[start_idx:full_idx + 1]
        print(f"\n--- 进全屏段 f{start_idx}..f{full_idx}（{len(grow)} 帧）---")
        prev_w = -1
        for k, (bbox, ratio, ts) in enumerate(grow):
            x, y, w_, h_ = bbox
            flag = ""
            if w_ < prev_w - 4:
                flag = "  ⛔宽度回退"
                ok = False
            if w_ > W + 4:
                flag += "  ⛔超出屏幕"
                ok = False
            print(f"f{start_idx + k:04d} ts={ts:7.0f}ms  {w_}x{h_}@({x},{y})  "
                  f"内容={ratio * 100:5.1f}%{flag}")
            prev_w = max(prev_w, w_)
        # (b) 每帧都有内容（空白窗 = 占比近 0）
        blanks = [(start_idx + k, ratio) for k, (_, ratio, _) in enumerate(grow)
                  if ratio < 0.005]
        if blanks:
            print(f"⛔ 进全屏段有 {len(blanks)} 帧内容占比 <0.5%（疑似空白窗）：{blanks}")
            ok = False
        else:
            print("✓ 进全屏段每帧都有内容（占比均 ≥0.5%）")

    # (c) 全程黑色空帧（bbox 空 = 纯黑 = 露桌面/黑屏）
    empty = [i for i, (b, _, _) in enumerate(rows) if b[2] == 0]
    # 录制首尾一定有空帧（应用还没起来 / 已退出），只看增长段附近窗口
    if start_idx is not None:
        lo = max(0, start_idx - 6)
        hi = min(n, (full_idx or n) + 12)
        mid_empty = [i for i in empty if lo <= i < hi]
        print(f"\n进全屏窗口 f{lo}..f{hi} 内黑色空帧：{len(mid_empty)}"
              f"（{mid_empty[:10]}）")
        if mid_empty:
            print("⛔ 有黑色空帧 = 露桌面")
            ok = False
        else:
            print("✓ 进全屏窗口内无黑色空帧（不露桌面）")

    # (d) 空闲快照抓屏耗时 & 落在动画之后否
    print("\n--- 进程内事件 ---")
    build_ev = [e for e in _EVENTS if "_build_fs_idle_pix" in e[1]]
    max_ret = next((t for t, tag in _EVENTS if tag == "startup_fullscreen() 返回"), None)
    esc_call = next((t for t, tag in _EVENTS if tag == "_on_escape() 调用"), None)
    first_fs = next((s["t"] for s in _SAMPLES if s.get("fs")), None)
    # ⛔ 进全屏覆盖层的收尾时刻：**只看到 _on_escape 之前**的采样。全程
    #    "最后一个可见覆盖层"是 3s 后还原动画那一段，拿它比会得出假结论。
    enter_cover_end = None
    for s in _SAMPLES:
        if esc_call is not None and s["t"] >= esc_call:
            break
        if s.get("cov") is not None or s.get("cov_vis"):
            enter_cover_end = s["t"]
    for t, tag in _EVENTS:
        if "build" in tag or "startup_fullscreen" in tag or "_on_escape" in tag:
            print(f"  {t:7.1f}ms  {tag}")
    # 关键状态变化（主窗 opacity / 覆盖层可见 / isFullScreen 变一次打一行）
    print("  --- 关键状态变化 ---")
    prev = None
    for s in _SAMPLES:
        cur = (s["fs"], round(s["op"], 1), s["cov_vis"],
               (s["cov"][2] if s["cov"] else 0))
        if cur != prev:
            cw = f"{s['cov'][2]}x{s['cov'][3]}" if s["cov"] else "None"
            print(f"    {s['t']:7.1f}ms fs={s['fs']} op={s['op']:.2f} "
                  f"cover_vis={s['cov_vis']} cover={cw}")
            prev = cur
    if first_fs is not None:
        print(f"  首个 isFullScreen 采样 = {first_fs:.1f}ms"
              f"（startup_fullscreen 返回于 {max_ret:.1f}ms）")
    if enter_cover_end is not None:
        print(f"  进全屏覆盖层最后可见采样 = {enter_cover_end:.1f}ms")
    # 采样间隔尖峰（>120ms 视为冻结）
    gaps = [(s["t"], s["dt"]) for s in _SAMPLES[1:] if s["dt"] > 120]
    print(f"  采样间隔 >120ms 的次数：{len(gaps)}")
    for t, d in gaps[:10]:
        print(f"    {t:7.1f}ms 间隔 {d:.0f}ms")
    if build_ev and enter_cover_end is not None:
        t_build = build_ev[0][0]
        if t_build > enter_cover_end:
            print(f"✓ 快照抓屏（{t_build:.0f}ms）在进全屏覆盖层动画收尾"
                  f"（{enter_cover_end:.0f}ms）之后，不在动画中段")
        else:
            print(f"⛔ 快照抓屏（{t_build:.0f}ms）早于覆盖层收尾"
                  f"（{enter_cover_end:.0f}ms），落在动画里")
            ok = False

    # 导出关键帧（增长段前后各几帧）供人工目检
    if start_idx is not None:
        lo = max(0, start_idx - 3)
        hi = min(n, (full_idx or n) + 4)
        cap = cv2.VideoCapture(rec)
        idx = 0
        while True:
            okr, frame = cap.read()
            if not okr:
                break
            if lo <= idx < hi:
                cv2.imwrite(os.path.join(OUT, f"f{idx:04d}.png"), frame)
            idx += 1
        cap.release()
        print(f"\n关键帧 PNG：{OUT}（f{lo:04d}..f{hi - 1:04d}）")

    print("\n=== 结论 ===")
    print("  最大化方向：", "✓ 通过" if ok else "✗ 有问题（见上方 ⛔）")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--app" in sys.argv:
        # 子进程：只跑 app（_quit 里 os._exit，不等 exec 返回）
        run_app()
        os._exit(0)
    # 父进程：先自己快照一份 QSettings 兜底（子进程可能被看门狗硬退、
    # 来不及还原），录制 + 分析，结束再兜底恢复一次。
    _st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: _st.value(k) for k in _st.allKeys()})
    _rc = 1
    try:
        _rec = os.path.join(OUT, "maximize_v2.mp4")
        if "--analyze-only" in sys.argv and os.path.exists(_rec):
            _rc = analyze(_rec)
        else:
            _rc = analyze(record())
    finally:
        try:
            _st2 = QSettings("MimoWorks", "SerialAssistant")
            for _k in set(_st2.allKeys()) - set(_SNAPSHOT):
                _st2.remove(_k)
            for _k, _v in _SNAPSHOT.items():
                _st2.setValue(_k, _v)
            _st2.sync()
            print("[兜底] QSettings 已按父进程快照恢复")
        except Exception as _exc:      # noqa: BLE001
            print(f"[warn] QSettings 兜底恢复失败：{_exc}")
    sys.exit(_rc)
