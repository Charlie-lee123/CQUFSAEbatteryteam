# -*- coding: utf-8 -*-
r"""开屏淡出「真窗口 alpha」验收探针（2026-11-06）。

⛔ 上一轮的翻车点：只用**离屏 QImage** 验证背景淡出，没走 QWidget 的
autoFillBackground 填充链，结果漏判 `WA_TranslucentBackground` 被自动填充
吃掉透明。本探针全程用 ``QT_QPA_PLATFORM=windows`` 的**真实窗口**，
用 ``QWidget.grab()`` 取像素（与 team-lead 的实验 A 同一口径）。

覆盖四件事
----------
1. 实验 A 2×2 复现：translucent × autoFill，确认「两者同开 → alpha 恒 255」。
2. **真实 `_SplashWindow`**（不调 start()，走 paintEvent 非 motion 淡出分支）：
   把 `_fade_k` 依次设成 0/0.3/0.6/0.9/1.0，同步 repaint 后 grab 中心像素，
   要求 alpha 严格递减到 0。并在**同一个窗口上**翻回旧配置
   （autoFill=True）做阳性对照，证明效果确实由本次改动带来。
3. `_fade_step(k=1)` 的功能验收：同步 repaint 是否真的在 `_close()` 之前
   画出全透明帧、撤场是否延后一拍、`_on_finished` 是否只触发一次。
4. 离屏 5 帧（splash_motion.MotionRenderer）：k=1 背景 alpha=0、无 logo
   残影；k=0.3~0.6 高斯模糊清晰可见（梯度能量下降）。
5. layered 合成开销 A/B：同尺寸 translucent 窗口 vs 不透明窗口，
   连续同步 repaint 的 ms/帧，用于判断它是否「整个动画段」的主因。

⛔ 不写 QSettings（开头全量快照、结尾只读比对）；不构造主窗；
   窗口都很小（≤640×400）且短命，尽量不干扰并行抓帧的 tail-flash-probe。

用法::

    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_probe_fade_alpha_real.py

产物：``_check/fade_alpha_real.txt``（数值报告）、
      ``_check/fade_alpha_real.png``（5 帧铺棋盘格的可视化）。
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRect, QSettings, Qt          # noqa: E402
from PySide6.QtGui import (QColor, QGuiApplication, QImage,  # noqa: E402
                           QPainter, QPalette)
from PySide6.QtWidgets import QApplication, QWidget      # noqa: E402

import numpy as np                                       # noqa: E402

K_SEQ = (0.0, 0.3, 0.6, 0.9, 1.0)
REPORT: list[str] = []


def say(line: str = "") -> None:
    print(line)
    REPORT.append(line)


def _settings_snapshot() -> dict:
    s = QSettings("MimoWorks", "SerialAssistant")
    return {k: s.value(k) for k in s.allKeys()}


def _center_alpha(widget: QWidget, app: QApplication) -> tuple[int, int, int, int]:
    """同步重绘后 grab 真窗口 backing store，返回中心像素 (a,r,g,b)。"""
    widget.repaint()
    app.processEvents()
    app.processEvents()
    img = widget.grab().toImage().convertToFormat(
        QImage.Format.Format_ARGB32)
    c = img.pixelColor(img.width() // 2, img.height() // 2)
    return c.alpha(), c.red(), c.green(), c.blue()


class _Ctrl(QWidget):
    """实验 A 的最小复刻：paintEvent 用带 alpha 的黑填满整窗。"""

    def __init__(self, translucent: bool, auto_fill: bool) -> None:
        super().__init__(None)
        if translucent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#ff0000"))
        self.setPalette(pal)
        self.setAutoFillBackground(auto_fill)
        self.resize(200, 140)
        self._a = 255

    def paintEvent(self, event) -> None:                 # noqa: ARG002
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, self._a))
        p.end()


def part_a(app: QApplication) -> bool:
    say("=" * 78)
    say("① 实验 A 复现（真实窗口 grab 中心像素；k=0 与 k=1 两端）")
    say("=" * 78)
    ok = True
    for transl in (True, False):
        for af in (True, False):
            w = _Ctrl(transl, af)
            w.show()
            app.processEvents()
            vals = []
            for k in (0.0, 1.0):
                w._a = int(round(255.0 * (1.0 - k)))
                a = _center_alpha(w, app)[0]
                vals.append(a)
            w.hide()
            w.deleteLater()
            app.processEvents()
            say(f"  translucent={transl!s:5} autoFill={af!s:5} "
                f"→ alpha(k=0)={vals[0]:3d}  alpha(k=1)={vals[1]:3d}")
            if transl and af and vals != [255, 255]:
                ok = False
            if transl and not af and vals != [255, 0]:
                ok = False
    say(f"  [判定] 实验 A 矩阵与既有结论一致：{'PASS' if ok else 'FAIL'}")
    return ok


def part_b(app: QApplication, preset: dict) -> bool:
    """真实 _SplashWindow 的淡出 alpha 序列 + 同一窗口上的阳性对照。"""
    import splash_anim

    say()
    say("=" * 78)
    say("② 真实 _SplashWindow（本次修复后）：_fade_k 0→1 的 alpha 序列")
    say("=" * 78)
    w = splash_anim._SplashWindow(preset)
    w.setGeometry(QRect(40, 40, 240, 160))   # ⚠️ 缩小，避免全屏干扰并行抓帧
    w.show()
    app.processEvents()
    transl = w.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    af_before = w.autoFillBackground()
    say(f"  WA_TranslucentBackground = {transl}（历史选择，必须保留）")
    say(f"  autoFillBackground       = {af_before}（本次改为 False）")
    say(f"  palette Window 底色仍设  = {w.palette().color(QPalette.ColorRole.Window).name()}")

    fixed = []
    for k in K_SEQ:
        w._fade_k = k
        a, r, g, b = _center_alpha(w, app)
        expect = int(round(255.0 * (1.0 - k)))
        fixed.append(a)
        say(f"    k={k:.1f} 期望 alpha≈{expect:3d} → 实测 {a:3d}  "
            f"RGB=({r:3d},{g:3d},{b:3d})  {'✓' if abs(a - expect) <= 2 else '✗'}")

    # 阳性对照：**同一个真窗口**切回旧配置 autoFill=True 再跑一遍
    w.setAutoFillBackground(True)
    old = []
    for k in K_SEQ:
        w._fade_k = k
        old.append(_center_alpha(w, app)[0])
    w.setAutoFillBackground(False)
    say(f"  阳性对照（同一窗口 autoFill=True，即修复前）：alpha 序列 {old}")
    w.hide()
    app.processEvents()

    dec = all(fixed[i] > fixed[i + 1] for i in range(len(fixed) - 1))
    near = all(abs(fixed[i] - int(round(255.0 * (1.0 - K_SEQ[i])))) <= 2
               for i in range(len(K_SEQ)))
    control = all(a == 255 for a in old)
    ok = bool(transl) and (not af_before) and dec and near and fixed[-1] == 0 and control
    say(f"  [判定] 单调递减={dec}  逐点吻合={near}  末帧 alpha=0={fixed[-1] == 0}  "
        f"旧配置恒 255={control}  → {'PASS' if ok else 'FAIL'}")
    return ok


def part_c(app: QApplication, preset: dict) -> bool:
    """k=1 时同步 repaint 先于撤场、撤场延后一拍、回调且仅一次。"""
    import splash_anim

    say()
    say("=" * 78)
    say("③ _fade_step(k=1)：全透明帧先上屏 + 延后一拍撤场 + 幂等")
    say("=" * 78)
    w = splash_anim._SplashWindow(preset)
    w.setGeometry(QRect(300, 40, 240, 160))
    w.show()
    app.processEvents()

    painted: list[float] = []
    orig_paint = w.paintEvent

    def counted(event) -> None:
        painted.append(time.perf_counter())
        orig_paint(event)

    w.paintEvent = counted
    fired = {"n": 0}
    w.set_on_finished(lambda: fired.__setitem__("n", fired["n"] + 1))

    class _FakeElapsed:
        """把墙钟钉在 1000ms（> _FADE_MS=300）→ k 必为 1.0。"""
        def elapsed(self) -> float:
            return 1000.0

    w._fade_elapsed = _FakeElapsed()
    w._fade_close_pending = False
    w._fade_step()

    n_sync = len(painted)
    pending = w._fade_close_pending
    closed_now = w._closed
    say(f"  调用 _fade_step() 后立刻（未跑事件循环）：同步 paintEvent 次数={n_sync}  "
        f"_fade_close_pending={pending}  _closed={closed_now}")
    say(f"  ⇒ k=1 的全透明帧{'已' if n_sync >= 1 else '未'}同步绘制；"
        f"撤场{'已延后' if not closed_now else '被立即执行（硬伤 B 未修）'}")

    # 让 singleShot 的撤场落地（一整个目标帧周期，最短 1ms）
    for _ in range(80):
        app.processEvents()
        if w._closed:
            break
        time.sleep(0.001)
    after = fired["n"]
    # 再调一次 _close()：幂等，回调不得重复
    w._close()
    twice = fired["n"]
    timer_stopped = w._fade_timer is None
    say(f"  撤场后：_closed={w._closed}  _on_finished 触发次数={after}  "
        f"再调 _close() 后={twice}  _fade_timer 已清空={timer_stopped}")
    ok = (n_sync >= 1 and pending and not closed_now and w._closed
          and after == 1 and twice == 1 and timer_stopped)
    say(f"  [判定] {'PASS' if ok else 'FAIL'}")
    return ok


def _bgra_array(img: QImage) -> np.ndarray:
    """QImage(ARGB32) → (h,w,4) BGRA（小端）。alpha 在 [:,:,3]。"""
    buf = img.constBits()
    arr = np.frombuffer(buf, dtype=np.uint8,
                        count=img.width() * img.height() * 4)
    return arr.reshape(img.height(), img.width(), 4)


def _luma(bgra: np.ndarray) -> np.ndarray:
    """亮度（logo 是白色墨迹叠在深色底上；⛔ 不能拿 alpha 通道量模糊，
    k=0/0.3 的底整幅不透明，alpha 恒 255，梯度恒 0 会假 FAIL）。"""
    b = bgra[:, :, 0].astype(np.float64)
    g = bgra[:, :, 1].astype(np.float64)
    r = bgra[:, :, 2].astype(np.float64)
    return 0.114 * b + 0.587 * g + 0.299 * r


def part_d() -> bool:
    """离屏 5 帧：k=1 零残影；0.3~0.6 高斯可见（梯度能量下降）。"""
    import splash_motion as sm

    say()
    say("=" * 78)
    say("④ 离屏 5 帧（MotionRenderer.paint，ARGB32，棋子底可见透明）")
    say("=" * 78)
    W, H = 900, 560
    r = sm.MotionRenderer("dark", 2.0, W, H, sm.SPEC_PLANA)
    for _ in range(80):
        if r._absorb_pending() and r._fade_pix is not None:
            break
        time.sleep(0.05)
    say(f"  _fade_pix（高品质高斯底图）就绪 = {r._fade_pix is not None}")

    from PIL import Image, ImageDraw
    from PIL.ImageQt import fromqimage

    rows = []
    images = []
    say(f"  {'k':>5}{'mean alpha':>12}{'max alpha':>11}{'非透明像素':>12}"
        f"{'亮度标准差':>13}{'亮度梯度能量':>15}")
    for k in K_SEQ:
        img = QImage(W, H, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        r.set_fade(k)
        r.paint(p, W, H, 2.5)
        p.end()
        bgra = _bgra_array(img)
        a = bgra[:, :, 3].astype(np.float64)
        lum = _luma(bgra)
        # logo 大致落在中部；相邻差分=高频能量，高斯模糊会显著削掉它
        roi = lum[H // 4:H * 3 // 4, W // 8:W * 7 // 8]
        gx = np.abs(np.diff(roi, axis=1)).mean()
        gy = np.abs(np.diff(roi, axis=0)).mean()
        energy = float(gx + gy)
        contrast = float(lum.std())
        non_zero = int((a > 0).sum())
        rows.append((k, float(a.mean()), float(a.max()), non_zero, energy, contrast))
        say(f"  {k:>5.1f}{a.mean():>12.2f}{a.max():>11.0f}{non_zero:>12d}"
            f"{contrast:>13.2f}{energy:>15.3f}")
        images.append(fromqimage(img).convert("RGBA"))

    # 棋盘格底：透明处显示灰白格，直观看到 k→1 整窗透明
    pad, cell = 8, 16
    scale = 0.42
    cw, ch = int(W * scale), int(H * scale)
    mont = Image.new("RGB", (5 * (cw + pad) + pad, ch + 2 * pad + 22), (24, 24, 28))
    draw = ImageDraw.Draw(mont)
    for i, im in enumerate(images):
        small = im.resize((cw, ch), Image.Resampling.LANCZOS)
        board = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        bd = ImageDraw.Draw(board)
        for y in range(0, ch, cell):
            for x in range(0, cw, cell):
                v = 205 if ((x // cell + y // cell) % 2 == 0) else 145
                bd.rectangle([x, y, x + cell, y + cell], fill=(v, v, v, 255))
        board.alpha_composite(small)
        px = pad + i * (cw + pad)
        mont.paste(board.convert("RGB"), (px, pad + 22))
        draw.text((px + 4, 4), f"k={K_SEQ[i]:.1f}", fill=(235, 235, 240))
    out = Path(ROOT) / "_check" / "fade_alpha_real.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    mont.save(out)
    say(f"  可视化已写 {out.relative_to(ROOT)}")

    e0, e03, e06 = rows[0][4], rows[1][4], rows[2][4]
    c03, c06 = rows[1][5], rows[2][5]
    last = rows[-1]
    # k=1：整幅 alpha 必须处处为 0（背景 + logo 都淡尽）
    zero = (last[1] == 0 and last[2] == 0 and last[3] == 0)
    # 0.3/0.6 的高斯：边缘高频被削掉、但墨迹仍可辨（亮度标准差没归零）
    blur = (e0 > 0.05 and e03 < e0 * 0.75 and e06 < e0 * 0.75
            and c03 > 3.0 and c06 > 3.0
            and rows[1][1] > 20 and rows[2][1] > 20)
    say(f"  [判定] k=1 整幅 alpha=0/无残影={zero}  "
        f"亮度梯度 k=0 {e0:.3f} → k=0.3 {e03:.3f} → k=0.6 {e06:.3f}"
        f"（高斯可见且仍可辨）={blur}  "
        f"→ {'PASS' if zero and blur else 'FAIL'}")
    return bool(zero and blur)


def part_e(app: QApplication) -> bool:
    """layered 合成开销：查原生 WS_EX_LAYERED + A/B 同步重绘 ms/帧。"""
    import ctypes
    from ctypes import wintypes

    say()
    say("=" * 78)
    say("⑤ WA_TranslucentBackground 的 layered 合成成本（A/B，真实窗口）")
    say("=" * 78)
    user32 = ctypes.windll.user32
    get_ex = user32.GetWindowLongW
    get_ex.argtypes = [wintypes.HWND, ctypes.c_int]
    get_ex.restype = ctypes.c_long
    gwl_exstyle, ws_ex_layered = -20, 0x00080000

    class _Timed(QWidget):
        def __init__(self, translucent: bool) -> None:
            super().__init__(None)
            if translucent:
                self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                                | Qt.WindowType.WindowStaysOnTopHint
                                | Qt.WindowType.Tool)
            self.setAutoFillBackground(False)

        def paintEvent(self, event) -> None:            # noqa: ARG002
            p = QPainter(self)
            # 模拟动画段：整窗不透明底色（k=0 的真实绘制），刻意不含高斯计算
            p.fillRect(self.rect(), QColor(13, 13, 15, 255))
            p.fillRect(QRect(80, 80, 160, 60), QColor(240, 240, 240, 255))
            p.end()

    def measure(size: tuple[int, int], transl: bool, n: int = 30) -> float:
        w = _Timed(transl)
        w.resize(*size)
        w.show()
        app.processEvents()
        w.repaint()
        t0 = time.perf_counter()
        for _ in range(n):
            w.repaint()
        dt = (time.perf_counter() - t0) / n * 1000.0
        w.hide()
        w.deleteLater()
        app.processEvents()
        return dt

    # 原生窗口样式：WA 是在 __init__ 里、show() 之前设的，若这里查到
    # WS_EX_LAYERED，就证明**整个动画段**（不止淡出 300ms）都走 layered 合成。
    probe = _Timed(True)
    probe.resize(320, 200)
    probe.show()
    app.processEvents()
    ex_tr = int(get_ex(wintypes.HWND(int(probe.winId())), gwl_exstyle))
    probe.hide()
    probe.deleteLater()
    app.processEvents()
    probe2 = _Timed(False)
    probe2.resize(320, 200)
    probe2.show()
    app.processEvents()
    ex_op = int(get_ex(wintypes.HWND(int(probe2.winId())), gwl_exstyle))
    probe2.hide()
    probe2.deleteLater()
    app.processEvents()
    say(f"  原生 WS_EX_LAYERED(0x{ws_ex_layered:05X}): "
        f"translucent=True → {'已置位' if ex_tr & ws_ex_layered else '未置位'}；"
        f"translucent=False → {'已置位' if ex_op & ws_ex_layered else '未置位'}")
    say("  ⇒ WA 在 __init__ 里设、作用于整个窗口生命周期：若已置位，"
        "则 3s 动画**每一帧**（不只淡出）都要走 UpdateLayeredWindow 的"
        "全屏 ARGB 表面提交。")

    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
    say(f"  屏 dpr={dpr:.1f}；每格 3 轮取中位，交替先后以消热机偏差")
    for size in ((640, 400), (1200, 750)):
        samples = {False: [], True: []}
        for rep in range(3):
            order = (False, True) if rep % 2 == 0 else (True, False)
            for tr in order:
                samples[tr].append(measure(size, tr))
        med = {tr: float(np.median(samples[tr])) for tr in (False, True)}
        ratio = med[True] / max(1e-6, med[False])
        say(f"  {size[0]}×{size[1]} 逻辑 / {int(size[0]*dpr)}×{int(size[1]*dpr)} 物理："
            f"不透明 {med[False]:.3f} ms/帧  layered {med[True]:.3f} ms/帧  "
            f"（×{ratio:.2f}）  样本 "
            f"{[round(x, 3) for x in samples[False]]} / "
            f"{[round(x, 3) for x in samples[True]]}")
    say("  ⚠️ 这是「窗口表面提交」成本，QWidget.paint() 本身另算；"
        "全屏 1600×1000 逻辑 @dpr2 = 3200×2000 物理 ≈25.6MB ARGB/帧，"
        "成本按面积放大，需真机 full-screen + splash_perf.log 的 stable_fps "
        "前后对照才能定性是否「主因」。本轮不改架构。")
    return bool(ex_tr & ws_ex_layered) and not bool(ex_op & ws_ex_layered)


def part_static() -> bool:
    say()
    say("=" * 78)
    say("⑥ 静态核对：淡出计时/帧率/autoFill，无硬编码 15ms 残留")
    say("=" * 78)
    src = (ROOT / "splash_anim.py").read_text(encoding="utf-8")
    import ui_anim
    hz = max(60, int(ui_anim.frame_hz()))
    interval = max(1, int(round(1000.0 / hz)) - 4)
    checks = [
        ("两处 setAutoFillBackground(False)",
         src.count("self.setAutoFillBackground(False)") == 2),
        ("无 setAutoFillBackground(True) 可执行语句",
         "self.setAutoFillBackground(True)" not in src),
        ("淡出用 QElapsedTimer 墙钟",
         "self._fade_elapsed = QElapsedTimer()" in src
         and "elapsed.elapsed() / float(_FADE_MS)" in src),
        ("淡出 timer 用 frame_hz() 同源",
         "from ui_anim import frame_hz" in src
         and "self._fade_timer.start(max(1, int(round(1000.0 / hz)) - 4))" in src),
        ("淡出 timer 是 PreciseTimer",
         "self._fade_timer.setTimerType(Qt.TimerType.PreciseTimer)" in src),
        ("k=1 同步 repaint + 延后撤场",
         "self.repaint()" in src
         and "QTimer.singleShot(max(1, int(round(1000.0 / hz))), self._close)" in src),
        ("_close 幂等守卫",
         "if self._closed:\n            return" in src),
        ("无 _fade_t 假累积（整词匹配，别误伤 _fade_timer）",
         re.search(r"\b_fade_t\b", src) is None),
        ("无 start(15) 固定周期", "start(15)" not in src),
    ]
    for name, ok in checks:
        say(f"  {'✓' if ok else '✗'} {name}")
    say(f"  frame_hz()={hz} → 淡出目标间隔 =max(1,round(1000/{hz})-4)={interval}ms "
        f"（与 _start_motion 同式）")
    return all(ok for _name, ok in checks)


def main() -> int:
    app = QApplication(sys.argv)
    snap_before = _settings_snapshot()

    preset = {"id": "preset:probe", "label": "probe",
              "path": str(ROOT / "assets" / "splash"
                          / "splash_dark_planb_4k60.mp4"),
              "kind": "video", "theme": "dark"}
    say(f"平台 = {os.environ.get('QT_QPA_PLATFORM')}（真实窗口，非离屏）")

    results = {
        "A 实验A 2×2": part_a(app),
        "B 真窗 alpha 序列": part_b(app, preset),
        "C k=1 同步上屏/延后/幂等": part_c(app, preset),
        "D 离屏 5 帧高斯与零残影": part_d(),
        "E layered 成本": part_e(app),
        "F 静态核对": part_static(),
    }

    snap_after = _settings_snapshot()
    clean = snap_after == snap_before
    say()
    say(f"QSettings 零污染（只读比对，全程未写）: {'PASS' if clean else 'FAIL'}  "
        f"keys={len(snap_before)} → {len(snap_after)}")
    ok = all(results.values()) and clean
    say()
    say("=" * 78)
    say("总判定：" + ("PASS" if ok else "FAIL")
        + "  " + "  ".join(f"{k}={'✓' if v else '✗'}" for k, v in results.items()))
    say("=" * 78)

    out = Path(ROOT) / "_check" / "fade_alpha_real.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(REPORT) + "\n", encoding="utf-8")
    print(f"\n报告已写 {out.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
