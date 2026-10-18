# -*- coding: utf-8 -*-
r"""QA 独立验收：三处动画的"跳变"判定（只读，不改源码）。

三个被验收的动画：
  ① 打开设置——**换帧后 300ms**（真面板上屏后的像素抖动）
  ② 接收区最大化（几何连续性）
  ③ 退出最大化（几何连续性）
  ④ 中途打断（展开到 ~40% 再点一次，反向必须平滑）
  ⑤ 帧率无关性（同一动画在 60Hz 与 165Hz 下墙钟时长差）

判据（写死，输出 PASS/FAIL + 数字）：
  G  几何 max/median —— **已作废**（中位被 1px 取整帧稀释，结构性不可达），仅 INFO 对照
  G' 几何（**正式判据**，按进度 Δq 归因）：跟踪斜率 max ≤ 1.4×行程（均值 ≤ 1.2×行程），
     且 **Δq==0 帧数 = 0**（同拍几何改两遍 = 二次落地的硬凭证）
  P  换帧：着陆后任意相邻两帧的**变化采样点数** < 500（4 物理像素网格、阈值 6）
  P2 落定闪：换帧路径**事件驱动拍屏**（wrap 4 方法 + DwmFlush 后拍落定区），呈现帧
     逐帧变化点无孤立峰（> 邻3帧中位×3 且 >800），Show 后换帧帧数 ≥8
  I  打断：反向后的第一帧位移**方向**与上一帧相反、幅度 ≤ 上一帧的 1.5 倍
  T  时长：60Hz 与 165Hz 下墙钟时长差 ≤ 15%
  S1 字体不干涉：退出最大化全程 `_port_label.width()` / `sizeHint()` / 字体签名恒定
  S2 提前预留开关位：首满宽 q ≤ 0.90 且此后钉住满量程、开关不压 `cmb_link`、不戳出卡右缘
  S3 回放开关：点「打开历史数据」有过渡、再点同一颗按钮 450ms 内关闭且渐缩

用法：
    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools\_qa_anim_verdict.py
    ... devtools\_qa_anim_verdict.py S      # 只跑 S1+S2+S3
    ... devtools\_qa_anim_verdict.py S2     # 只跑 S2
产物：_check/anim_verdict.txt
"""
from __future__ import annotations

import ctypes
import math
import os
import statistics
import sys
import time
from ctypes import wintypes

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_HERE = os.path.dirname(os.path.abspath(__file__))     # devtools/ 自己也要在路径上
if _HERE not in sys.path:                               # （from _desktop_gate import …）
    sys.path.insert(0, _HERE)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QEvent, QObject, QTimer, QPoint, QRect, QSettings  # noqa: E402
from PySide6.QtGui import QImage                                     # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog                 # noqa: E402

# ----------------------------------------------------------------- 判据
# G 的 3× max/median **已作废**：中位被 1px 取整帧稀释到 0.3~0.9px/ms，诚实曲线
# 也算 8~15×，结构性不可达。正式判据是 G′；这个数只在 INFO 里打印对照。
SPEED_RATIO_MAX = 3.0
# ⚠️ G 的 3× 门槛对 `STANDARD`（cubic-bezier(.2,0,0,1)）**天然不公平**：这条曲线的
#    峰值斜率实测 **4.04**、而导数中位数≈1 → 一条完全诚实的动画，max/median 本来
#    就有 ≈4 倍。所以另加 G'（按曲线理论峰值归一）作为"真跳变"的判据。
# G′ 跟踪斜率门槛（lead 2026-10-19 定稿 A 案）：`_rx_t` 是 Tween 曲线**之后**的
# 进度，几何对它线性跟踪 → speed=|Δgeo|/(Δq×duration) 的分母换了时钟，165Hz
# 采样相位差在分子分母同时出现、自动约掉 —— 旧墙钟 dt 版本 1.44~1.58× 的超限
# 全部消失。参照值 = **1.0×行程**（完美跟踪），不是曲线前理论峰值 4.68（那两者
# 不同钟，0.30× 的"余量"是虚的）。
TRACK_SLOPE_MAX_TOL = 1.4      # 最大跟踪斜率 ≤ 1.4×行程（实测 ~1.23：布局取整 1px
#                                + 发送卡 show() 那拍的多推；非跳变——Δq==0=0、P/P2 全 0 点）
TRACK_SLOPE_MEAN_TOL = 1.2     # 均值跟踪斜率 ≤ 1.2×行程（ΣΔgeo/ΣΔq/行程，防整体偏移）
TRACK_SLOPE_MEAN_TOL_LAYOUT = 1.25   # 多事件布局类控件（功能列/工具栏行）：rail 宽度
#                                      与内部布局双重驱动、小步多，均值门槛放宽档
_TRACK_LAYOUT_WIDGETS = {"功能列", "工具栏行"}
_TRACK_KNOWN_DEV = {"串口开关"}   # max 斜率记已知偏差（lead 2026-10-19 终裁）：
#                                  地板停 4px 不再上爬；PASS(dev) 单列不进 FAIL
#                                  分母，护栏见 analyse_curve_normalised 尾部
TRACK_SETTLE_MAX_PX = 4.0      # Δq==0 且 Δgeo≤4px = 收尾 settle（_settle_rx_layout
#                                的收尾布局），单独计数、不算跳变；>4px 才是结构跳变
TRACK_MAX_MIN_GEO_PX = 4.0     # max 斜率的 Δgeo 地板（lead 2026-10-19 批准，取代
#                                旧 2px 档）：出事的对全是 3~4.1px/Δq0.004~0.01 的
#                                取整毛刺——4px 在 214px 行程里占 1.9%、肉眼不可见，
#                                拿它算"峰值速度"没有物理意义。⚠️ 地板=4px（**可见性
#                                下限**），再往上提必须过 lead；每次剔除照印明细，
#                                真 5px 以上的跳变仍会被抓。
TRACK_MAX_MIN_DQ = 0.01        # max 斜率还要求 |Δq|≥0.01（lead 批准的地板）：Δq≈0.004
#                                的拍点 1px 取整放大出 ~700px/单位q 假斜率
TRACK_STEP_TOL = 1.4           # **绝对单步**判据（堵 Δq 地板的盲区）：单步位移 ≤
#                                TRACK_STEP_TOL × 行程 × CURVE_PEAK × (16ms/260ms)。
THROTTLE_STEP_MS = 16.0        # ⚠️ 必须含 CURVE_PEAK≈4.04：一个
#                                16ms 节流步在曲线最陡处的位移 = 行程×4.04×0.0615
#                                （380px 行程 → 94px），漏掉曲线斜率的 33px 门槛
#                                连诚实曲线都过不了（实测峰值步 98~111px，恰好
#                                = 94px×1.1~1.18 的 dt 归因抖动）。修正后的门槛
#                                ~132px：正常步过、真跳变（108px@301px行程 →
#                                按比例 ≈136px@380px）仍被抓住 —— 数学与实测
#                                双重验证，lead 的 33px 推导漏了曲线斜率因子


def _curve_peak_slope() -> float:
    """数值微分求 STANDARD 的峰值斜率（Δ进度 / Δ归一化时间）。"""
    from ui_anim import STANDARD
    return max((STANDARD((k + 1) / 2000.0) - STANDARD(k / 2000.0)) * 2000.0
               for k in range(2000))


CURVE_PEAK = _curve_peak_slope()
TRACK_STEP_CURVE_FACTOR = CURVE_PEAK   # 单步门槛的曲线斜率因子（见上方 TRACK_STEP_TOL 注）
PIXELS_CHANGED_MAX = 500       # P：变化采样点数
PIXELS_MOVING = 20             # S3②：一帧算"画面在动"的变化点数下限（滤噪点）
PIXEL_GRID = 4                 # P：采样网格（物理像素）
PIXEL_THRESHOLD = 6            # P：单点判定为"变了"的灰度阈值
INTERRUPT_AMP_MAX = 1.5        # I：反向首帧幅度 / 上一帧幅度
DURATION_DELTA_MAX = 0.15      # T：60Hz 与 165Hz 时长相对差
SETTINGS_LAND_MS = 300.0       # ① 换帧后观测窗口
P2_CAPTURE_MS = 760.0          # P2 兜底（定时器连拍）：自点击起拍多久
P2_SETTLE_WINDOW = (520.0, 700.0)   # P2 兜底判定窗：工程师实测真实 Show≈559ms、
#                                     落定区像素活动 560~680ms，窗口必须盖住它
P2_LAND_PAD = 40               # P2：落定区 = _settings_geom_to 外扩这么多 px
P2_MIN_FADE_STEPS = 8          # P2：渐隐步数下限（in_out_cubic 96ms @120Hz ≈ 12~15 步）
#                               pix_run11 起改判「调用记账的去重 value 数」而非抓帧数：
#                               单次全落定区抓屏 ~50-300ms（DwmFlush+GetDIBits+两趟
#                               十万级亮扫+grid=4 变化点）远贵于 96ms 的坡本身 ——
#                               事件驱动抓帧必然 <8，那是测量把现象吃了，不是现象没了。
#                               补间的真实形状由零扰动的调用记账 + alpha 序列证明。
P2_HIDE_MAX_POINTS = 2500      # P2：撤帧帧 vs 撤帧前最后一帧的变化点上限。
#                               撤帧前帧在 set_alpha<=0.02 时抓（坡尾、快照已
#                               透明/已陈旧）；新实现此项应只剩阴影/标题栏残差带
#                               的尾部；旧实现对照是 86400 点 / 均差 45.11 的
#                               整场压暗硬切，本门槛取其 ~1/34。
P2_AFTER_MAX_POINTS = 2500     # P2：撤帧后稳定性帧（vs 撤帧前帧）的变化点上限。
#                               pix_run13 实测 AA 地板 ~1200 点（两次抓屏的次像素
#                               合成差异，三桶集中在 1~8）；旧实现的「撤帧后」是
#                               暗面→真面板的整块硬跳（量级 >>2500），门槛仍分得开。
P2_DIM_MIN_RATIO = 0.5         # P2：撤帧前最后态均亮 ≥ 撤帧后稳定态的 50%（纯相对）。
#                               ⚠️ 原设的绝对地板「均亮 >60」2026-10-19 已废：
#                               设置面板是**深色主题**，稳定桌面上落定区均亮稳定
#                               ~30.8（FINAL/pix_run15 两次独立实测，PNG 实证面板
#                               本色即暗），>60 对该内容永不可达——绝对门槛量的是
#                               「面板主题亮不亮」，不是「渐隐有没有压暗」。pix_run14
#                               量到的 84.8 是桌面 flicker 期间的伪影（落定区外 40px
#                               环带被抬亮，环带占 23%：0.77×31+0.23×255≈82），非面板
#                               本色。压暗的机理签名（旧实现 45.1→20.8→3.0→0.2→0.0
#                               线性趋黑）由相对判据捕获：旧实现 pre_hide 3.9 vs 稳定态
#                               30.8 = 12.7% < 50% 仍 FAIL，鉴别力不受影响。
P2_ZONE_W = 620                # P2 兜底抓取区（逻辑像素）——双区同尺寸
P2_ZONE_H = 760                #      （面板 504×661 + ~40px 阴影环 + 余量）
P2_TARGET_FPS = 30             # P2 兜底：低于它就在 INFO 里提示采样密度不足
P2_SPIKE_FACTOR = 3.0          # P2：孤立峰 = 本帧变化点 > 邻帧中位 × 3
P2_SPIKE_ABS = 800             # P2：且绝对值 > 800 点（4px 网格上的可见量级）
P2_NEIGHBOURS = 3              # P2："邻帧"取前后各 3 帧
REPLAY_BAR_FULL = 50           # S3：回放提示条的满高（硬占位 50px）
REPLAY_LAST_CHANGE_MS = (200.0, 232.0)   # S3①：末变 ∈ 窗（证明 220ms 补间跑满；
#                                          末变与曲线无关，是"跑满没跑满"主锚点）
REPLAY_FIRST2PX_MAX_MS = 40.0  # S3①：首个 ≥2px 可见变化 ≤40ms（EASE 曲线的验收点，
#                                in_out_cubic 时代是 56ms 必挂，换 EASE 后应 ~24ms）
REPLAY_MIN_STEPS = 8          # S3①：不同高度数下限（EASE 理论 13 个台阶）
# ⚠️ S3① 全部时间以 **Tween.start 打戳**为 0（补间真正起跑），不是 _open_history
#    调用时刻 —— _enter_replay 前缀（抓图+settle 重排）有 ~40ms 空档，不扣掉
#    会把前缀当动画（lead 2026-10-19 终裁：150~450 span 口径作废）。
S2_HOLD_Q = 0.90               # S2-1：q≥0.90 全程卡宽==满量程（让位并钉住）
REPLAY_ANIM_MS = (150.0, 450.0)     # S3：过渡时长窗口
REPLAY_CLOSE_MS = 450.0        # S3：二次点击后多久必须收起

TRACK = (("接收卡", "_rx_card"), ("日志区", "txt_rx"), ("功能列", "_rx_rail"),
         ("串口开关", "_open_row"), ("发送卡", "_tx_card"),
         ("品牌页头", "brand_header"), ("配置卡", "_config_card"),
         ("工具栏行", "_rx_opts_row"))

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


def pump_until(app, predicate, timeout_ms: float = 3000.0) -> bool:
    end = time.perf_counter() + timeout_ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.001)
    return False


# ------------------------------------------------- 逐帧几何取样（挂在 ticker 上）

class GeometryTap:
    """把取样挂在 ``ui_anim._Ticker._tick`` 上：**每推进一帧补间就取一次**。

    与动画严格同拍，采到的位移才是真实的（轮询采样会把两帧并成一帧，看起来像跳）。
    """

    def __init__(self, app, window) -> None:
        self.app = app
        self.window = window
        self.rows: list[tuple[float, dict]] = []
        self._recording = False
        self._orig = None
        self._ticker = None

    def _sample(self) -> dict:
        # `_q` 与几何**同一拍**读（都在 tick 之后）：G′ 的 Δq 归因要求分子分母同源
        try:
            snap = {"_q": float(self.window._rx_t)}
        except (AttributeError, RuntimeError):
            snap = {"_q": None}
        for short, attr in TRACK:
            w = getattr(self.window, attr, None)
            if w is None:
                snap[short] = None
                continue
            try:
                p = w.mapToGlobal(QPoint(0, 0))
                g = w.geometry()
                snap[short] = (p.x(), p.y(), g.width(), g.height(),
                               1 if w.isVisible() else 0)
            except RuntimeError:
                snap[short] = None
        return snap

    def __enter__(self):
        import ui_anim
        self._ticker = ui_anim._ticker()
        self._orig = self._ticker._tick

        def patched():
            self._orig()
            if self._recording:
                self.rows.append((time.perf_counter(), self._sample()))

        self._ticker._tick = patched
        return self

    def __exit__(self, *_exc):
        if self._ticker is not None and self._orig is not None:
            self._ticker._tick = self._orig
        return False

    def run(self, action, settle_ms: float = 1400.0) -> list[tuple[float, dict]]:
        self.rows = []
        self._recording = True
        t0 = time.perf_counter()
        action()
        # 等补间跑完（取样在 tick 里，这里只负责泵事件）
        deadline = t0 + settle_ms / 1000.0
        import ui_anim
        started = False
        while time.perf_counter() < deadline:
            self.app.processEvents()
            active = ui_anim.ticker_active()
            started = started or active
            if started and not active:
                break
            time.sleep(0.001)
        self._recording = False
        self.app.processEvents()
        return self.rows


MIN_FRAME_MS = 3.5          # 比这更近的两次 tick 视为**同一视觉帧**（合并）


def _merge_subframes(rows, min_dt_ms: float = MIN_FRAME_MS):
    """把间隔 < min_dt_ms 的相邻采样合并成一帧。

    ⚠️ 为什么必须合并：补间计时器标称 6ms（165Hz），但一次重绘卡住之后会出现
    "追赶式"连发 —— 实测 43 个间隔里 5 个 <4ms（最小 1.7ms），而中位数是 10.7ms。
    这种两连发是**一次视觉帧**被拆成了两个 tick，不合并的话速度会被算大好几倍。
    """
    if len(rows) < 2:
        return list(rows)
    out = [rows[0]]
    for t, snap in rows[1:]:
        if (t - out[-1][0]) * 1000.0 >= min_dt_ms:
            out.append((t, snap))
        else:
            out[-1] = (out[-1][0], snap)        # 保留前一时刻、用后一几何
    return out


def _motion_min_dt() -> float:
    """事件分组阈值：max(3.5, 当前帧间隔)。

    165Hz 时 = 6ms：比这更近的两次采样若几何都变了，视为同一次更新的双采样
    （保留旧时刻、更新几何），否则速度会被 6ms 底数放大 2~3 倍。
    """
    import ui_anim
    return max(MIN_FRAME_MS, float(ui_anim.frame_interval_ms()))


def _geo_dist(a, b) -> float:
    """几何位移量：位置 (x,y) 与尺寸 (w,h) **各取欧氏距离**、再取 max。

    为什么不用曼哈顿 |Δx|+|Δy|：斜着飞的控件（串口开关退出时 x −143 / y +161）
    曼哈顿每步都比首末直线距离多 ~41%，均值被系统性抬到 1.24×行程
    （lead 2026-10-19 裁定改欧氏）。位置/尺寸取 max 是为了兼管"纯缩放"控件
    （接收卡 w/h 在变、位置跟随）—— 两个维度哪个动了都算。
    """
    dpos = math.hypot(b[0] - a[0], b[1] - a[1])
    dsize = math.hypot(b[2] - a[2], b[3] - a[3])
    return max(dpos, dsize)


def _widget_motion(rows, short: str, min_dt_ms: float):
    """按「几何变化事件」提取单个控件的运动（G/G′ 共用）。

    根因（工程师实测、lead 认可）：接收区补间被 ``min_interval_ms=16`` 节流到
    ≈60fps，而取样是 165Hz（6ms）→ 一次 76px 的几何更新被 2~3 个采样点分摊，
    速度落在 6ms 底数上被放大 2.7×（扫阈值：3.5ms→14.04px/ms、8ms→5.10、
    16ms→4.83）。这是**取样口径的结构性放大**，不是运动变了。
    所以：连续相同的几何 = 同一次更新（吞掉中间采样点）；速度 = Δ几何 /
    (本次变化时刻 − 上次变化时刻)。76px/16ms = 4.75px/ms，与曲线理论峰值 4.04 自洽。

    返回 ``(speeds, deltas, dts, span_px)``；``span_px`` 只在首末几何同为可见时给。
    """
    events: list[tuple[float, tuple]] = []
    for t, snap in rows:
        s = snap.get(short) if isinstance(snap, dict) else None
        if s is None:
            continue
        if events and s == events[-1][1]:
            continue                            # 几何没变 = 同一次更新，吞掉
        if events and s[4] != events[-1][1][4]:
            events.append((t, s))               # 显隐翻转：记事件、不算速度
            continue
        if events and (t - events[-1][0]) * 1000.0 < min_dt_ms:
            events[-1] = (events[-1][0], s)     # 同帧双采样：保留旧时刻、更新几何
            continue
        events.append((t, s))
    speeds: list[float] = []
    deltas: list[float] = []
    dts: list[float] = []
    for (t1, a), (t2, b) in zip(events, events[1:]):
        dt = max(0.1, (t2 - t1) * 1000.0)
        d = _geo_dist(a, b)
        speeds.append(d / dt)
        deltas.append(d)
        dts.append(dt)
    span = 0.0
    if len(events) >= 2:
        a, b = events[0][1], events[-1][1]
        if a[4] == b[4]:
            span = _geo_dist(a, b)
    return speeds, deltas, dts, span


def _widget_motion_q(rows, short: str, min_dt_ms: float):
    """按「几何变化事件」提取运动，**按进度 Δq 归因**（lead 2026-10-19 定稿）。

    跟踪斜率 = Δgeo / |Δq| —— Δq（``window._rx_t``，**曲线后**进度）与 Δgeo 在
    **同一拍**成对读到（``GeometryTap._sample`` 在每个 tick 之后取），采样相位
    差在分子分母同时出现、自动约掉。墙钟 dt 版本的坑：几何在 tween 送值时刻变、
    时间戳却记在采样时刻，两者最多差 6ms（165Hz）→ 16ms 真实步长被量成
    12.5~13.8ms、速度放大 1.15~1.28×（实测 1.44~1.58× 的超限全来自这）。

    **Δq==0 硬判据（分档，lead 2026-10-19 终裁）**：相邻两个几何变化事件之间
    Δq==0（同一拍里几何改了两遍）——
    - Δgeo ≤ 4px → **收尾 settle**（``_settle_rx_layout`` 的收尾布局，补间停了
      几何才落，不算跳变，单独计数）；
    - Δgeo > 4px → **结构跳变**（"两刀落地"的精确签名，FAIL）。

    返回 ``(pairs, dq_zero, span, events, qs)``：
    - ``pairs``：[(Δgeo, |Δq|, t1, t2, Δpos, Δsize)]，|Δq|>1e-6 的事件对；
    - ``dq_zero``：[(t1, t2, Δgeo, Δpos, Δsize, q1, q2)]（时间已换成相对
      ``rows[0]`` 的 ms），分档由调用方做；Δpos/Δsize 分解用来判别"位置跳"
      还是"尺寸跳"，t2−t1 用来判别落在补间中段还是收尾边界；
    - ``span``：首末事件几何的欧氏位移（只在首末同为可见时给）。
    """
    t0 = rows[0][0] if rows else 0.0
    events: list[tuple[float, tuple]] = []      # 与 _widget_motion 同一合并规则
    qs: list[float] = []
    for t, snap in rows:
        s = snap.get(short) if isinstance(snap, dict) else None
        q = snap.get("_q") if isinstance(snap, dict) else None
        if s is None or q is None:
            continue
        if events and s == events[-1][1]:
            continue                            # 几何没变 = 同一次更新，吞掉
        if events and s[4] != events[-1][1][4]:
            events.append((t, s))               # 显隐翻转：记事件、不算速度
            qs.append(float(q))
            continue
        if events and (t - events[-1][0]) * 1000.0 < min_dt_ms:
            events[-1] = (events[-1][0], s)     # 同帧双采样：保留旧时刻，几何/q 取后拍
            qs[-1] = float(q)
            continue
        events.append((t, s))
        qs.append(float(q))
    pairs: list[tuple[float, float, float, float, float, float]] = \
        []   # (Δgeo, |Δq|, t1, t2, Δpos, Δsize)
    dq_zero: list[tuple[float, float, float, float, float, float, float]] = []
    for i, ((_t1, a), (t2, b)) in enumerate(zip(events, events[1:])):
        dq = qs[i + 1] - qs[i]
        d = _geo_dist(a, b)
        if abs(dq) < 1e-6:
            dpos = math.hypot(b[0] - a[0], b[1] - a[1])
            dsize = math.hypot(b[2] - a[2], b[3] - a[3])
            dq_zero.append(((events[i][0] - t0) * 1000.0,
                            (t2 - t0) * 1000.0, d, dpos, dsize,
                            qs[i], qs[i + 1]))
            continue                            # 收尾 settle / 结构跳变：调用方分档
        dpos = math.hypot(b[0] - a[0], b[1] - a[1])
        dsize = math.hypot(b[2] - a[2], b[3] - a[3])
        pairs.append((d, abs(dq), events[i][0], t2, dpos, dsize))
    span = 0.0
    if len(events) >= 2:
        a, b = events[0][1], events[-1][1]
        if a[4] == b[4]:
            span = _geo_dist(a, b)
    # events/qs 一起带回，给累积口径结案判据用（见 _cum_check）
    return pairs, dq_zero, span, events, qs


def analyse_geometry(rows) -> list[tuple[str, bool, str, float]]:
    """G（已**作废**，仅 INFO 对照）：max(速度) ≤ 3 × median(速度)。

    作废原因：中位被 1px 取整帧稀释到 0.3~0.9px/ms，任何诚实曲线都算出 8~15×，
    结构性不可达。正式判据是 G′（见 ``analyse_curve_normalised``）；这里保留
    max/median 打印是为了和旧基线对照。
    """
    min_dt = _motion_min_dt()
    out = []
    for short, _attr in TRACK:
        speeds, deltas, dts, _span = _widget_motion(rows, short, min_dt)
        moving = [v for v in speeds if v > 0.005]
        if len(moving) < 4:
            out.append((short, True, "基本没动（无需判定）", 0.0))
            continue
        med = statistics.median(moving)
        worst = max(speeds)
        ratio = worst / med if med > 0 else float("inf")
        idx = speeds.index(worst)
        out.append((short, ratio <= SPEED_RATIO_MAX,
                    f"max={worst:.3f}px/ms med={med:.3f} ratio={ratio:.2f} "
                    f"峰值帧 {deltas[idx]:.0f}px/{dts[idx]:.1f}ms", ratio))
    return out


# --------------------------------------------------------------- 屏幕取样（GDI）

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class ScreenGrabber:
    """1:1 抓屏幕的一块（**物理像素**：逻辑矩形必须乘 devicePixelRatio）。"""

    def __init__(self, logical_rect: QRect, dpr: float) -> None:
        x = int(round(logical_rect.x() * dpr))
        y = int(round(logical_rect.y() * dpr))
        self.width = max(1, int(round(logical_rect.width() * dpr)))
        self.height = max(1, int(round(logical_rect.height() * dpr)))
        self._x, self._y = x, y
        hdc_screen = u32.GetDC(0)
        self.mem = g32.CreateCompatibleDC(hdc_screen)
        self.bmp = g32.CreateCompatibleBitmap(hdc_screen, self.width, self.height)
        g32.SelectObject(self.mem, self.bmp)
        u32.ReleaseDC(0, hdc_screen)
        self._bmi = _BITMAPINFO()
        self._bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        self._bmi.bmiHeader.biWidth = self.width
        self._bmi.bmiHeader.biHeight = -self.height
        self._bmi.bmiHeader.biPlanes = 1
        self._bmi.bmiHeader.biBitCount = 32
        self._bmi.bmiHeader.biCompression = BI_RGB
        self._buf = ctypes.create_string_buffer(self.width * self.height * 4)

    def grab(self):
        hdc = u32.GetDC(0)
        try:
            ok = g32.BitBlt(self.mem, 0, 0, self.width, self.height,
                            hdc, self._x, self._y, SRCCOPY)
        finally:
            u32.ReleaseDC(0, hdc)
        if not ok:
            return None
        g32.GetDIBits(self.mem, self.bmp, 0, self.height, self._buf,
                      ctypes.byref(self._bmi), DIB_RGB_COLORS)
        return self._buf.raw

    def close(self) -> None:
        g32.DeleteObject(self.bmp)
        g32.DeleteDC(self.mem)


def changed_points(a: bytes, b: bytes, width: int, height: int,
                   grid: int = PIXEL_GRID,
                   threshold: int = PIXEL_THRESHOLD) -> int:
    """两帧之间"变化采样点"的个数（``grid`` 物理像素网格）。

    先用 ``bytes`` 相等挡掉"整帧没变"（着陆后绝大多数帧都相同，那是一次 memcmp），
    真的不同才做逐点统计 —— 否则每帧几十万次 Python 循环会把采样拖垮。
    """
    if not a or not b or len(a) != len(b) or width <= 0:
        return 0
    if a == b:
        return 0
    row_bytes = width * 4
    stride = max(4, grid * 4)
    count = 0
    for y in range(0, height, grid):
        base = y * row_bytes
        for i in range(base, base + row_bytes, stride):
            if abs(a[i] - b[i]) > threshold:
                count += 1
    return count


def _delta_stats(a: bytes, b: bytes, width: int, height: int,
                 grid: int = PIXEL_GRID, threshold: int = PIXEL_THRESHOLD
                 ) -> tuple[int, float]:
    """两帧之间（网格采样）的变化点数 + 变化点的**平均单通道差**。

    ``changed_points`` 的带均值版 —— P2 撤帧判据要报的是 lead 的口径
    「均差 ≤1」（旧实现 86400 点 / 均差 45.11），光有点数不够。同一遍循环
    里顺手累加，不增加遍历成本；整帧相等时走 memcmp 快路。
    """
    if not a or not b or len(a) != len(b) or width <= 0:
        return 0, 0.0
    if a == b:
        return 0, 0.0
    row_bytes = width * 4
    stride = max(4, grid * 4)
    count = 0
    total = 0
    for y in range(0, height, grid):
        base = y * row_bytes
        for i in range(base, base + row_bytes, stride):
            d = abs(a[i] - b[i])
            if d > threshold:
                count += 1
                total += d
    return count, (total / count if count else 0.0)


# ------------------------------------------------- 桌面活性门禁（防假 PASS）

def _screen_alive() -> tuple[bool, str]:
    """桌面活性门禁（lead 2026-10-19 紧急定，**防假 PASS 的硬前置**）。

    锁屏 / 显示器休眠 / 无 DWM 合成时 BitBlt 返回**纯黑**，像素判据会得到
    "逐帧变化点 0"的假 PASS —— 那不是没有变化，是没有画面（当天 P2/S3② 的
    全 0 全是假象）。判定口径照 lead 的整屏实测（1600×1000 抽 228572 点）：

        max_luma > 24 且 nonzero% > 5 且 uniq_colors > 8   →  ALIVE

    ⚠️ 2026-10-18 修过一个自己的坑：原实现只抓屏幕**中心 400×300**，当应用
    窗口铺满屏幕（startup_maximized）时，中心区整个落在应用自己的平坦 UI
    （空日志区，246/1 色）里 → 误报 BLACK、连 SKP 三轮。现在**整屏**抓取，
    直接复用工程师的共用门禁 ``_desktop_gate.desktop_metrics``（判据同源、
    数字同口径 —— lead 连采 18 次用的就是它），不再有第二套实现走样。
    """
    from _desktop_gate import desktop_metrics
    max_luma, pct, colors, w, h = desktop_metrics()
    detail = (f"max_luma={max_luma} nonzero%={pct:.2f} "
              f"uniq_colors={colors}")
    alive = (max_luma > 24 and pct > 5.0 and colors > 8)
    return alive, detail


def _frame_luma(buf: bytes) -> int:
    """单帧 max_luma 抽样（步长 16 字节，>128 早退）—— 判这一帧是不是黑的。"""
    if not buf:
        return 0
    best = 0
    for i in range(0, len(buf) - 2, 16):
        luma = (buf[i + 2] * 299 + buf[i + 1] * 587 + buf[i] * 114) // 1000
        if luma > best:
            best = luma
            if best > 128:
                break
    return best


def _frame_mean_luma(buf: bytes) -> float:
    """单帧**平均亮度**（步长 16 字节，0~255）。

    为什么单独要它：max_luma 只能判"有没有画面"，判不了"画面被整体压暗"
    ——设置面板渐隐那个 bug 的形状就是**均亮随 alpha 单调掉到 0**（面板被
    渐成黑场），max 那一路看不出来（窗口别处还有亮像素）。旧/新实现对照
    就靠这一列。
    """
    if not buf:
        return 0.0
    total = 0
    n = 0
    for i in range(0, len(buf) - 2, 16):
        total += (buf[i + 2] * 299 + buf[i + 1] * 587 + buf[i] * 114) // 1000
        n += 1
    return total / max(1, n)


def _env_report(tag: str = "", retries: int = 3, wait_s: float = 20.0) -> tuple[bool, str]:
    """检测并打印 `[环境] 桌面活性` 一行（对外可引用的前置条件）。

    BLACK 时等 ``wait_s`` 秒重试，最多 ``retries`` 次（lead 2026-10-19：防
    "刚好撞上休眠瞬间"——显示器的休眠/唤醒在秒级反复横跳时，第一次读数可能
    落在黑窗里）。最终 BLACK 则像素判据一律 SKIP，绝不 PASS。
    """
    alive, detail = _screen_alive()
    tries = 0
    while not alive and tries < retries:
        tries += 1
        info("[环境] 桌面活性", f"第 {tries} 次读到 BLACK（{detail}），"
             f"等 {wait_s:.0f}s 重试（最多 {retries} 次）")
        time.sleep(wait_s)
        alive, detail = _screen_alive()
    suffix = f"；重试 {tries} 次后" if tries else ""
    info("[环境] 桌面活性" + (f"（{tag}）" if tag else ""),
         f"{'ALIVE' if alive else 'BLACK'}（{detail}）{suffix}"
         + ("" if alive else "；像素判据一律 SKIP，绝不记 PASS"))
    return alive, detail


def measure_settings_landing(app, window, grabber) -> list[tuple[float, int, int]]:
    """着陆后 SETTINGS_LAND_MS 内，逐帧算"与上一帧的变化采样点数"。

    返回 ``(t, points, max_luma)`` —— luma 用于黑屏甄别（中途锁屏的帧标记
    INVALID 并剔除，见 P 场景）。
    """
    frames: list[tuple[float, int, int]] = []
    prev: bytes | None = grabber.grab()
    prev_lum = _frame_luma(prev) if prev is not None else 0
    t0 = time.perf_counter()
    while (time.perf_counter() - t0) * 1000 < SETTINGS_LAND_MS:
        app.processEvents()
        cur = grabber.grab()
        if cur is None:
            time.sleep(0.002)
            continue
        lum = _frame_luma(cur)
        # 黑帧后重置 diff 链基线：黑→活的转换会被误算成巨量「变化点」
        base = prev if (prev is not None and prev_lum > 24) else None
        frames.append(((time.perf_counter() - t0) * 1000,
                       0 if base is None else
                       changed_points(base, cur, grabber.width, grabber.height),
                       lum))
        prev = cur
        prev_lum = lum
        time.sleep(0.002)
    return frames


# ------------------------------------------------- P2 设置落定闪（连拍）

class _PaintTap(QObject):
    """**全局**事件过滤器：记设置面板一族的 Show / Hide / Paint / Expose。

    ⚠️ 三个要点（lead 2026-10-18 定，修上轮"落定没有新 Show/Paint"的漏记）：
    1. 判据用 ``isinstance(obj, QDialog)`` **或类名** ``_SettingsShot``——
       ``SettingsDialog`` 的 ``objectName()`` 是空字符串，按 objectName 过滤
       会整个放过它；而快照宿主 ``_SettingsShot`` 不是 QDialog，它的
       Paint/Hide（6 步渐隐 96ms 后撤走）也是证据链的一环，必须一起记。
    2. 装在 ``QApplication`` 上、**点击之前**——只包 ``__init__`` 那一次的话，
       落定路径如果复用了旧实例就不会再走 ``__init__``，过滤期就断了。
    3. 记 Show / Hide / Paint / Expose 四种，输出按时间排序的
       ``(t_ms, 事件名, 类名, visible)`` —— 一眼看出"有没有重新映射"。
    """

    _NAMES = {QEvent.Type.Show: "Show", QEvent.Type.Hide: "Hide",
              QEvent.Type.Paint: "Paint", QEvent.Type.Expose: "Expose"}

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[float, str, str, bool]] = []

    def eventFilter(self, obj, event):
        kind = self._NAMES.get(event.type())
        if kind is None:
            return False
        try:
            name = type(obj).__name__
            if not (isinstance(obj, QDialog) or name.startswith("_SettingsShot")):
                return False
            self.events.append((time.perf_counter(), kind, name,
                                bool(obj.isVisible())))
        except RuntimeError:
            pass
        return False                    # 不拦截，正常派发


def _install_paint_tap():
    """点击**之前**在 ``QApplication`` 上全局装 ``_PaintTap``（跑完记得卸）。"""
    tap = _PaintTap()
    app = QApplication.instance()
    if app is not None:
        app.installEventFilter(tap)
    return tap


def _p2_zone_centered(window, center) -> QRect:
    """620×760 逻辑像素、以 ``center``（屏幕坐标）为中心，夹进屏幕。

    按 devicePixelRatio 换算成物理矩形、全程 1:1 BitBlt 不缩放 —— 每区每帧约
    2.4MB（整窗全屏态是 25MB，成本全在 ``GetDIBits`` 拷贝上，收益为零）。
    """
    rect = QRect(0, 0, P2_ZONE_W, P2_ZONE_H)
    rect.moveCenter(center)
    screen = QApplication.primaryScreen().availableGeometry()
    if rect.right() > screen.right():
        rect.moveRight(screen.right())
    if rect.bottom() > screen.bottom():
        rect.moveBottom(screen.bottom())
    if rect.left() < screen.left():
        rect.moveLeft(screen.left())
    if rect.top() < screen.top():
        rect.moveTop(screen.top())
    return rect.intersected(screen)


def _p2_zone_button(window) -> QRect:
    """P2 抓取区之一（lead 指定）：以设置按钮为中心的 620×760（动画起点）。"""
    return _p2_zone_centered(window, window.mapToGlobal(
        window._settings_button_rect().center()))


def _p2_zone_center(window) -> QRect:
    """P2 兜底抓取区之二：以**窗口中心**为中心的 620×760 —— 面板落定区
    （``_settings_geom_to`` 504×661）在这里面。按钮居中区盖不住落定区（按钮在
    右上、面板居中，几何上就不可能），所以双 grabber 都从 t=0 起。
    """
    return _p2_zone_centered(window, window.frameGeometry().center())


def _p2_zone_landing(window) -> QRect | None:
    """落定区（主判据区）= ``_settings_geom_to`` **外扩 40px**，夹进屏幕。

    ``_settings_geom_to`` 是**屏幕坐标**（``dialog.frameGeometry()``，见
    main_window 里对两套坐标系混用的注释），所以不用再做窗口平移。
    """
    geom_to = getattr(window, "_settings_geom_to", None)
    if geom_to is None or geom_to.isEmpty():
        return None
    rect = QRect(geom_to).adjusted(-P2_LAND_PAD, -P2_LAND_PAD,
                                   P2_LAND_PAD, P2_LAND_PAD)
    screen = QApplication.primaryScreen().availableGeometry()
    return rect.intersected(screen)


class _SettingsSwapTap:
    """换帧取证（lead 2026-10-19 裁决）：**零扰动捕坡 + 稀疏结构帧**。

    pix_run11 的教训（本类 2026-10-19 重写的原因）：在渐隐坡的**路上**抓屏，
    主线程一次堵 ~50-300ms（DwmFlush + 1.7MB GetDIBits + 两趟十万级亮扫 +
    grid=4 变化点统计），96ms 的坡被三次结构性抓屏吃光 —— 补间从 0.0 直接
    跳 1.0，6553 点硬切全压在撤帧那一帧。**那是测量把现象吃了，不是现象没了**
    （同构建隔离复现 ``_qa_p2_fade_repro.py``：不抓屏时 15 个步 0.0→1.0 跑满
    ~100ms，机制本身好的）。

    所以抓屏策略改成（见 ``_want_shoot`` / ``_make_alpha_wrapper``）：
    · ``_fade_settings_step``：**全程不抓**，只记账（value 序列零扰动）——
      pix_run12/13 实测：坡路上任何一张抓屏（~50-300ms，本机负载高时
      ~450ms）都会把坡的剩余部分整段吃掉（调用记账：0.482 之后直接 1.0；
      alpha 轮询：0.518 之后直接 1.0）。v2 的「中段帧」因此量到的
      「撤帧硬切」其实是**被吃掉的半段坡**（压暗对话框 → 全亮对话框），
      不是撤帧本身；中段帧的「压暗」也是真面板首帧尚未合成上屏的时序差，
      不是旧 bug 的趋黑；
    · ``_on_settings_geometry_done``（仅开场路）：在**原函数之前**抓一张
      ``pre_show``（几何补间刚完、快照满不透明、真面板还没 show）——
      那一刻没有任何东西在动，抓屏只把整个换帧推迟几百 ms，不吃坡；
    · ``_settings_shot.set_alpha``：value ≤ 0.02 时抓一张 ``pre_hide``
      （坡已跑完、快照 alpha≈0）。**新实现**这一帧快照透明不可见
      （``setWindowOpacity(0)`` → DWM 按 alpha 混合，拍到的是真面板）；
      **旧实现**这一帧是陈旧的暗面（paintEvent 在 ``_alpha<=0`` 时直接
      return、窗口又非 layered，最后一帧的 multiply-dark 以全不透明
      合成）。所以「撤帧帧 vs 撤帧前帧」在新旧实现上判若两物 ——
      阳性对照的鉴别力就在这一帧上；
    · ``_finish_settings_swap``：抓（撤帧帧）。
    坡的真实形状（步数 / 每步 value / 时刻）由**调用记账** + 8ms alpha
    轮询证明 —— 两者都不抓屏，扰动为零。撤帧后再补两张稳定性帧
    （``snapshot``，+0.3s / +0.6s，diff 基线换回 pre_hide：撤帧本身
    已被 ② 量走，④ 量的是「撤帧之后还有什么在变」）。

    ⚠️ 两个还原陷阱：
    1. ``_fade_settings_step`` 以 ``self._fade_settings_step(...)`` 动态查
       实例属性，所以**无论它是 `QTimer.singleShot` 的 lambda 还是补间的
       lambda**（2026-10-18 修 P2 黑场时改成了后者），包上一样生效；
    2. ``_on_settings_geometry_done`` 在 ``Tween.__init__`` 时就绑定进了
       ``_on_finished`` —— 光还原实例属性不够，必须连 tween 的
       ``_on_finished`` 一起换、跑完再换回去。
    """

    _METHODS = ("_start_settings_swap_fade", "_fade_settings_step",
                "_finish_settings_swap", "_on_settings_geometry_done")

    def __init__(self, window, dpr: float) -> None:
        self.frames: list[tuple[float, int, str]] = []
        self.calls: list[tuple[str, int, float | None]] = []  # 每次方法调用记账
        self.lumas: list[int] = []              # 与 frames 平行；黑帧 INVALID 用
        self.alphas: list[tuple[float, str, float | None]] = []  # 拍点时刻的快照 alpha
        self.means: list[float] = []                # 与 frames 平行；均亮（判压暗）
        self.deltas: list[float] = []               # 与 frames 平行；本帧变化点的平均通道差
        self.error: str | None = None
        self.geom_txt = "?"
        self._window = window
        self._dpr = dpr
        self._t0 = time.perf_counter()
        self._prev: bytes | None = None
        self._saved: list[tuple[str, object, bool]] = []
        self._saved_shot: list[tuple[str, object, bool]] = []  # 快照对象的还原账
        self._shot_obj: object | None = None   # 被包 set_alpha 的快照（还原用）
        self._saved_tween_done: object | None = None
        self._grabber: ScreenGrabber | None = None
        self._pre_done = False               # 撤帧前结构帧已抓（只抓一张）
        self._pre_bytes: bytes | None = None  # 撤帧前帧原图（④ 的 diff 基线）
        # 尖峰取证目录（环境变量 QA_P2_DUMP 设置了才存 PNG，平时不影响跑批）
        self._dump_dir = os.environ.get("QA_P2_DUMP", "") or None
        # ⚠️ 落定区**不能在这里**读：``_settings_geom_to`` 是点击进行中
        # （``_animate_settings_geometry``）才落值的，构造时它是空 QRect。
        # grabber 挪到第一次拍点时惰性建（见 _shoot）。
        for name in self._METHODS:
            fn = getattr(window, name, None)
            if fn is None:
                self.error = f"main_window 没有 {name}（版本不对？）"
                self.uninstall()
                return
            self._saved.append((name, window.__dict__.get(name),
                                name in window.__dict__))
            setattr(window, name, self._make_wrapper(
                fn, name, shoot_before=(name == "_on_settings_geometry_done")))
        tween = getattr(window, "_settings_geom_tween", None)
        if tween is not None and getattr(tween, "_on_finished", None) is not None:
            self._saved_tween_done = tween._on_finished
            tween._on_finished = window._on_settings_geometry_done
        # 撤帧前那一帧挂在**快照自己的** set_alpha 上（value<=0.02 = 坡已跑完、
        # 快照已透明（新）或已陈旧（旧））。快照是常驻对象，这里包实例属性，
        # uninstall 时摘掉 —— 与 _METHODS 同一套账，只是对象不同。
        shot = getattr(window, "_settings_shot", None)
        fn_shot = getattr(shot, "set_alpha", None) if shot is not None else None
        if fn_shot is not None:
            self._shot_obj = shot
            self._saved_shot.append(("set_alpha", shot.__dict__.get("set_alpha"),
                                     "set_alpha" in shot.__dict__))
            shot.set_alpha = self._make_alpha_wrapper(fn_shot)

    def _make_wrapper(self, original, name, shoot_before=False):
        def wrapper(*args, **kwargs):
            # pre_show：几何补间刚完、快照满不透明、真面板未 show —— 全场唯一
            # 「没有任何东西在动」的时刻，抓屏只推迟换帧、不吃坡（仅开场路；
            # 收场路走同一回调但 _settings_geom_opening 为假，不抓）。
            if (shoot_before and name == "_on_settings_geometry_done"
                    and getattr(self._window, "_settings_geom_opening", False)):
                self._shoot("pre_show")
            result = original(*args, **kwargs)
            # 无论是否抓屏，调用本身都要记账 —— 渐隐步骤是「没被调用」、
            # 「被测量压缩」还是「帧被丢」，全靠这列分辨（pix_run10/11 排查）
            val = (round(float(args[1]), 4) if len(args) > 1
                   and isinstance(args[1], (int, float)) else None)
            self.calls.append((name, len(args), val))
            if self._want_shoot(name, val):
                self._shoot(name)
            return result
        wrapper.__name__ = f"wrapped_{name}"
        return wrapper

    def _make_alpha_wrapper(self, original):
        """包 ``_SettingsShot.set_alpha``：坡跑完（value≤0.02）时抓撤帧前帧。

        新实现：``setWindowOpacity(0)`` 之后快照透明不可见，这一帧拍到的是
        真面板（撤帧只是收走一个看不见的窗口）。旧实现：``_alpha`` 归 0、
        paintEvent 直接 return，窗口非 layered —— 表面留着最后一帧的暗图、
        以全不透明合成，这一帧拍到的是陈旧暗面。两者之差就是 ②③④ 的判据。
        """
        def wrapper(*args, **kwargs):
            result = original(*args, **kwargs)
            val = args[0] if args else kwargs.get("value")
            if (isinstance(val, (int, float)) and val <= 0.02
                    and not self._pre_done):
                self._pre_done = True
                self._shoot("pre_hide")
            return result
        wrapper.__name__ = "wrapped_set_alpha"
        return wrapper

    def _want_shoot(self, name: str, val: float | None) -> bool:
        """抓屏策略（pix_run12/13 实证后定，理由见类文档string）。

        · ``_finish_settings_swap``：抓 —— 撤帧帧，判据核心；
        · ``_fade_settings_step``：**一律不抓** —— 坡路上抓屏=吃坡（实测把
          0.482→1.0 的剩余半段整段吃掉，「撤帧硬切」量到的是半段坡）；
        · 撤帧前那一帧由 ``_make_alpha_wrapper`` 在 set_alpha<=0.02 时抓；
        · 其余只记账不抓。
        """
        return name == "_finish_settings_swap"

    def snapshot(self, label: str) -> None:
        """撤帧后的稳定性抓拍（+0.3s / +0.6s 各一张）。

        证明撤帧那一刻之后没有二次跳变（阴影重算、标题栏激活态迟到那类）。
        diff 基线换回 **pre_hide**（撤帧前最后可见态）：撤帧本身已被 ② 量走，
        ④ 要量的是「撤帧之后还有什么在变」。没拍到 pre_hide（坡被取消）就
        沿用调用链（vs 撤帧帧）。复用同一 grabber，与结构帧同表。
        """
        if self._pre_bytes is not None:
            self._prev = self._pre_bytes
        self._shoot(label)

    def _shoot(self, name: str) -> None:
        if self._grabber is None:
            rect = _p2_zone_landing(self._window)
            if rect is None:
                self.error = "第一次拍点时仍拿不到 _settings_geom_to"
                return
            self.geom_txt = (f"({rect.x()},{rect.y()} "
                             f"{rect.width()}×{rect.height()})")
            self._grabber = ScreenGrabber(rect, self._dpr)
        try:
            ctypes.windll.dwmapi.DwmFlush()     # 等这一帧真的合成完
        except Exception:                       # noqa: BLE001  没 dwmapi 就直接拍
            pass
        cur = self._grabber.grab()
        if cur is None:
            return
        t = (time.perf_counter() - self._t0) * 1000.0
        lum = _frame_luma(cur)
        self.means.append(_frame_mean_luma(cur))    # 均亮：判"被整体压暗"
        # 黑帧后重置 diff 链基线：黑→活的转换会被误算成巨量「变化点」
        # （final_run3 的 83265 点尖峰即此伪影），跳过该帧的 diff 即可。
        if self._prev is None:
            n, dmean = 0, 0.0
        else:
            n, dmean = _delta_stats(self._prev, cur,
                                    self._grabber.width, self._grabber.height)
        self.frames.append((t, n, name))
        self.deltas.append(dmean)              # 本帧变化点的平均通道差（lead 的「均差」口径）
        self.lumas.append(lum)                  # 黑屏甄别：锁屏中途开始整段作废
        if name == "pre_hide" and lum > 24:
            self._pre_bytes = cur               # ④ 的 diff 基线（撤帧前最后可见态）
        self.alphas.append((t, name,
                            getattr(getattr(self._window, "_settings_shot",
                                            None), "_alpha", None)))
        if n > 800 and self._prev is not None:
            self._spike_forensics(t, name, n, cur)
        self._prev = None if lum <= 24 else cur

    def _spike_forensics(self, t: float, name: str, n: int, cur: bytes) -> None:
        """尖峰取证（工程师 2026-10-19 定的测量顺序，PNG 只在要图时拍）：

        1. |Δ| 三桶直方图（1~8 / 9~32 / >32，全像素逐点最大通道差）+ 变化点
           mean|Δ| —— 三桶集中 1~8 = PrintWindow 灰度 AA vs 屏幕次像素 AA 的
           AA 级残差（fade 其实正常）；集中 >32 = 真内容不同；双峰 = 两者都有。
        2. 行/列 top5 分布 —— 集中一条带 = 某控件/装饰层；铺满 = 全局位移/残差。
        """
        w, h = self._grabber.width, self._grabber.height
        prev = self._prev
        row_bytes = w * 4
        b1 = b2 = b3 = 0
        dsum = 0
        for i in range(0, h * row_bytes, 4):
            m = max(abs(prev[i] - cur[i]), abs(prev[i + 1] - cur[i + 1]),
                    abs(prev[i + 2] - cur[i + 2]))
            if m > 32:
                b3 += 1
                dsum += m
            elif m > 8:
                b2 += 1
                dsum += m
            elif m >= 1:
                b1 += 1
                dsum += m
        grid = PIXEL_GRID
        rows = [sum(1 for x in range(0, w, grid)
                    if max(abs(prev[y * row_bytes + x * 4] - cur[y * row_bytes + x * 4]),
                           abs(prev[y * row_bytes + x * 4 + 1] - cur[y * row_bytes + x * 4 + 1]),
                           abs(prev[y * row_bytes + x * 4 + 2] - cur[y * row_bytes + x * 4 + 2])) > PIXEL_THRESHOLD)
                for y in range(0, h, grid)]
        cols = [sum(1 for y in range(0, h, grid)
                    if max(abs(prev[y * row_bytes + x * 4] - cur[y * row_bytes + x * 4]),
                           abs(prev[y * row_bytes + x * 4 + 1] - cur[y * row_bytes + x * 4 + 1]),
                           abs(prev[y * row_bytes + x * 4 + 2] - cur[y * row_bytes + x * 4 + 2])) > PIXEL_THRESHOLD)
                for x in range(0, w, grid)]
        mean_d = dsum / max(1, b1 + b2 + b3)
        info(f"P2/尖峰取证 @{t:.0f}ms（{name}）",
             f"判据阈值=单通道>{PIXEL_THRESHOLD}（判得 {n} 点）；全像素最大通道差"
             f"三桶：1~8={b1} / 9~32={b2} / >32={b3}；变化点 mean|Δ|={mean_d:.1f}"
             f"（末帧视觉幅度≈mean|Δ|/6，alpha=1/6 是最后可见态）；"
             f"行 top5={sorted(enumerate(rows), key=lambda p: -p[1])[:5]}"
             f"（y×{grid} 起）；列 top5={sorted(enumerate(cols), key=lambda p: -p[1])[:5]}"
             f"（x×{grid} 起）")
        if self._dump_dir:
            os.makedirs(self._dump_dir, exist_ok=True)
            tag = f"{int(t):04d}_{name}"
            for label, buf in (("prev", prev), ("cur", cur)):
                ba = bytearray(buf)
                ba[3::4] = b"\xff" * (w * h)    # DIB 的 X 字节强制成不透明
                img = QImage(bytes(ba), w, h, w * 4, QImage.Format.Format_ARGB32)
                img.save(os.path.join(self._dump_dir, f"{tag}_{label}.png"))
            info(f"P2/尖峰取证 @{t:.0f}ms", f"PNG 已存 {self._dump_dir}/{tag}_*.png")

    def uninstall(self) -> None:
        for name, value, had in reversed(self._saved):
            if had:
                setattr(self._window, name, value)
            else:
                self._window.__dict__.pop(name, None)
        self._saved.clear()
        for name, value, had in reversed(self._saved_shot):
            target = self._shot_obj
            if target is None:
                break
            if had:
                setattr(target, name, value)
            else:
                target.__dict__.pop(name, None)
        self._saved_shot.clear()
        tween = getattr(self._window, "_settings_geom_tween", None)
        if self._saved_tween_done is not None and tween is not None:
            tween._on_finished = self._saved_tween_done
            self._saved_tween_done = None
        if self._grabber is not None:
            self._grabber.close()
            self._grabber = None


def scenario_p2_settings_flash(app, window, dpr: float) -> None:
    """P2「设置落定闪」（lead 2026-10-19 二次裁决；pix_run11 后改**零扰动捕坡**）。

    为什么 P 测不到：P 先等 ``swap_target is None`` 且快照隐藏（≈112ms 之后）才
    开始抓屏再 pump 80ms —— 出事的那段正好被跳过。

    采样：``_SettingsSwapTap`` 包装换帧路径四个方法 + 快照的 ``set_alpha``。
    **抓屏策略见该类**——pix_run11/12/13 实证坡路上任何抓屏都会把 96ms 的
    渐隐吃掉（v2 的中段帧把 0.482→1.0 整段吃没，「撤帧硬切」量到的是半段
    坡），所以 v3 起坡路零抓屏：只补 pre_show（几何补间刚完）/pre_hide
    （坡跑完 set_alpha≤0.02）/撤帧/撤帧后两张结构帧；坡的真实形状由调用
    记账 + 8ms alpha 轮询（均不抓屏）证明。

    判据（v3 起全部以 **pre_hide（撤帧前最后可见态）** 为基准；旧「抓帧数
    ≥8 / 密集孤立峰 / 中段帧对比」判据已废止）：
    ① 渐隐步数 ≥8（调用记账的去重 value 数，0.0 起 1.0 收，零扰动）；
    ② 撤帧无硬切：撤帧帧 vs pre_hide 的变化点 ≤2500 且报均差
       （新实现此时快照已透明，撤帧只是收走看不见的窗口；旧实现 pre_hide
       是陈旧的暗面，撤帧=整块硬跳，对照 86400 点 / 均差 45.11）；
    ③ 渐隐段无整体压暗：pre_hide 均亮 ≥撤帧后稳定态的 50%
       （纯相对判据，见 ``P2_DIM_MIN_RATIO`` 注——绝对门槛 >60 已废：深色
       面板稳定桌面实测 ~30.8，量的是主题亮度不是压暗；旧实现同段
       45.1→20.8→3.0→0.2→0.0 线性趋黑，pre_hide 即趋黑末端，3.9/30.8=12.7%
       在相对判据下仍 FAIL）；
    ④ 撤帧后无二次跳变（+0.3s / +0.6s 两帧 vs pre_hide ≤2500；
       pix_run13 实测 AA 地板 ~1200 点，旧实现同项量级 >>2500）。
    另打 Show/Hide/Paint/Expose 事件序列做旁证（不参与判定）。

    兜底：包装装不上（方法缺失 / 拿不到 geom_to）才退回 QTimer 连拍
    （``_p2_fallback_timer``，760ms / 判定窗 [520,700]）。
    """
    if window._settings_dialog is not None:
        close_settings(app, window)
    pump(app, 0.2)

    # 桌面活性门禁：黑屏时拍到的"逐帧 0 变化"是假 PASS，一律 SKIP
    if not _env_report("P2 前")[0]:
        skip("P2/渐隐步数 ≥8", "桌面全黑，渐隐判据不可信")
        skip("P2/撤帧无硬切", "桌面全黑，像素判据不可信")
        skip("P2/渐隐段无整体压暗", "桌面全黑，像素判据不可信")
        skip("P2/撤帧后无二次跳变", "桌面全黑，像素判据不可信")
        return

    taps = _install_paint_tap()
    t0 = time.perf_counter()
    swap = _SettingsSwapTap(window, dpr)
    # 快照 alpha 序列（工程师 2026-10-19，8ms 便宜轮询）：坡在跑 = alpha 逐拍
    # 走 1.0→5/6→…→1/6；坡被吞 = 一步到底（中间值一个没有）。这列最便宜、
    # 最一刀见血，配合尖峰三桶直方图就能定性，不用等 PNG。
    alpha_log: list[tuple[float, float | None]] = []
    alpha_timer = QTimer()
    alpha_timer.setInterval(8)

    def _poll_alpha() -> None:
        alpha_log.append((time.perf_counter(),
                          getattr(getattr(window, "_settings_shot", None),
                                  "_alpha", None)))

    alpha_timer.timeout.connect(_poll_alpha)
    try:
        if swap.error:
            info("P2/事件拍屏", f"装不上（{swap.error}），退回定时器连拍兜底")
            _p2_fallback_timer(app, window, dpr, taps, t0)
            return
        alpha_timer.start()
        window.btn_settings.click()

        def settled():
            if window._settings_swap_target is not None:
                return False
            try:
                return not window._settings_shot.isVisible()
            except RuntimeError:
                return True

        pump_until(app, settled, 2500)
        pump(app, 0.05)                     # 兜底：最后一拍 DwmFlush 之后的收尾帧
        # 撤帧后稳定性两连拍（+0.3s / +0.6s）：证明撤帧那一刻之后没有二次跳变
        # （阴影重算、标题栏激活态迟到那类）。在 uninstall 之前拍，diff 链与
        # 结构帧连续；没抓到任何帧（grabber 没建成）就跳过，不阻塞分析。
        if swap.frames:
            pump(app, 0.3)
            swap.snapshot("post+0.3s")
            pump(app, 0.3)
            swap.snapshot("post+0.6s")
        frames = list(swap.frames)
        geom_to_txt = swap.geom_txt
    finally:
        alpha_timer.stop()
        swap.uninstall()
        _app = QApplication.instance()
        if _app is not None:
            _app.removeEventFilter(taps)

    try:
        # 黑帧甄别：luma ≤ 24 的帧标记 INVALID 并剔除（中途锁屏也能发现）
        valid = [f for f, lv in zip(frames, swap.lumas) if lv > 24]
        n_black = len(frames) - len(valid)
        alive2, ad2 = _screen_alive()           # 拍完复核
        if len(valid) < 3:
            skip("P2/渐隐步数 ≥8",
                 f"黑帧剔除后只剩 {len(valid)} 帧（拍完复核 {ad2}）"
                 + (f"；原因：{swap.error}" if swap.error else ""))
            skip("P2/撤帧无硬切", "桌面拍屏期间全黑，像素判据不可信")
            skip("P2/渐隐段无整体压暗", "桌面拍屏期间全黑，像素判据不可信")
            skip("P2/撤帧后无二次跳变", "桌面拍屏期间全黑，像素判据不可信")
            return

        info("P2/结构帧序列（v3：坡路零抓屏；pre_show/pre_hide/撤帧/撤帧后×2）",
             f"{len(valid)} 有效帧（剔除黑帧 {n_black}）；拍完复核 "
             f"{'ALIVE' if alive2 else 'BLACK'}；落定区 {geom_to_txt}；逐帧：")
        print("      t_ms   拍点                    变化点   均差   均亮   快照alpha")
        for i, (t, n, m) in enumerate(valid):
            al = next((a[2] for a in swap.alphas
                       if a[1] == m and abs(a[0] - t) < 0.5), None)
            mean = swap.means[i] if i < len(swap.means) else float("nan")
            dm = swap.deltas[i] if i < len(swap.deltas) else float("nan")
            print(f"    {t:6.0f}  {m:<24}  {n:6d}  {dm:5.1f}  {mean:6.1f}  "
                  f"{'' if al is None else format(al, '.3f')}")

        # 旁证（工程师 2026-10-19）：快照 alpha 序列 —— 判"96ms 坡是否真的逐拍
        # 上屏"。零扰动（只读属性），与调用记账互相印证。
        seq = []
        last = None
        for ts, al in alpha_log:
            if al is None:
                continue
            if last is None or abs(al - last) > 0.005:
                seq.append((round((ts - t0) * 1000.0), al))
                last = al
        info("P2/快照 alpha 序列（8ms 轮询，只列变化点）",
             f"{len(seq)} 个不同值："
             + "；".join(f"{al:.3f}@{ta}ms" for ta, al in seq[:14])
             + ("…" if len(seq) > 14 else ""))
        tap_alphas = [(t, m, al) for t, m, al in swap.alphas
                      if al is not None and ("_fade" in m or "_finish" in m)]
        if tap_alphas:
            info("P2/拍点时刻 alpha（事件拍点自带）",
                 "；".join(f"{m}@{t:.0f}ms={al:.3f}" for t, m, al in tap_alphas))

        # 排查记账（pix_run10/11）：每次方法调用都记了（丢帧的调用也记）——
        # 渐隐步骤是「没被调用」「被测量压缩」还是「帧被丢」，这列直接分晓。
        step_vals = [c[2] for c in swap.calls if c[0] == "_fade_settings_step"]
        if swap.calls:
            info("P2/方法调用记账（丢帧的调用也记）",
                 f"共 {len(swap.calls)} 次："
                 + "；".join(f"{c[0][1:]}"
                             f"{'' if c[2] is None else f'={c[2]:g}'}"
                             for c in swap.calls[:24])
                 + ("…" if len(swap.calls) > 24 else "")
                 + f"｜_fade_settings_step 共 {len(step_vals)} 次，"
                 + f"value 序列 {[round(v, 3) for v in step_vals[:16]]}")

        # ① 渐隐机制在跑（零扰动判据）：去重 value 数 ≥8、0.0 起 1.0 收。
        #    抓帧数必然少（单帧 ~50-300ms >> 96ms 坡）—— 坡的形状以这列为准。
        uniq = sorted(set(step_vals))
        ok_steps = (len(uniq) >= P2_MIN_FADE_STEPS and bool(step_vals)
                    and step_vals[0] == 0.0 and step_vals[-1] >= 1.0)
        check(f"P2/渐隐步数 ≥{P2_MIN_FADE_STEPS}（去重 value、0.0→1.0）", ok_steps,
              f"_fade_settings_step {len(step_vals)} 次调用 / {len(uniq)} 个去重值"
              f"（{[round(v, 3) for v in uniq[:12]]}"
              f"{'…' if len(uniq) > 12 else ''}）；"
              f"alpha 轮询另录 {len(seq)} 个变化点")

        # ②③④ 全部以 **pre_hide（撤帧前最后可见态 = 坡跑完、set_alpha<=0.02
        #    那一刻抓的帧）** 为基准（v3 理由见类文档string）：
        #    · 新实现：坡跑完 → setWindowOpacity(0) → 快照透明不可见 →
        #      pre_hide 拍到的就是真面板，撤帧只是收走一个看不见的窗口；
        #    · 旧实现：_alpha 归 0 后 paintEvent 直接 return、窗口非 layered →
        #      pre_hide 拍到的是**陈旧的暗面**（最后一帧 multiply-dark 以全
        #      不透明合成），撤帧 = 暗面→真面板的整块硬跳（工程师修前实测：
        #      86400 点 / 均差 45.11，均亮 45.1→20.8→3.0→0.2→0.0 线性趋黑）。
        fin = next((i for i, f in enumerate(valid)
                    if f[2] == "_finish_settings_swap"), None)
        pre = next((i for i, f in enumerate(valid)
                    if f[2] == "pre_hide"), None)
        if fin is None or pre is None or pre >= fin:
            skip("P2/撤帧无硬切",
                 "没拍到撤帧帧、或没拍到撤帧前帧（pre_hide：坡跑完 "
                 "set_alpha<=0.02 那一刻）—— 坡若被取消/没收尾本项不判；"
                 "若渐隐步数同时不达标，那是测量把坡吃了，不是现象没了")
        else:
            _t_f, n_f, _m_f = valid[fin]
            d_f = swap.deltas[fin] if fin < len(swap.deltas) else float("nan")
            check("P2/撤帧无硬切（撤帧帧 vs 撤帧前帧）",
                  n_f <= P2_HIDE_MAX_POINTS,
                  f"撤帧帧 {n_f} 点 / 均差 {d_f:.1f}"
                  f"（vs 撤帧前帧 @ {valid[pre][0]:.0f}ms，快照 alpha≈0；"
                  f"门槛 ≤{P2_HIDE_MAX_POINTS} 点；"
                  "旧实现同项 86400 点 / 均差 45.11 整场硬跳）")
            mean_pre = swap.means[pre] if pre < len(swap.means) else float("nan")
            post_idx = [i for i, f in enumerate(valid)
                        if i > fin and f[2].startswith("post")]
            mean_post = min((swap.means[i] for i in post_idx),
                            default=float("nan"))
            ok_dim = (mean_pre == mean_pre
                      and (mean_post != mean_post
                           or mean_pre >= P2_DIM_MIN_RATIO * mean_post))
            check("P2/渐隐段无整体压暗（撤帧前最后态不趋黑）", ok_dim,
                  f"撤帧前帧均亮 {mean_pre:.1f} vs 撤帧后稳定态 {mean_post:.1f}"
                  f"（门槛 ≥稳定态 {P2_DIM_MIN_RATIO:.0%}，纯相对判据；"
                  "旧实现同段 45.1→20.8→3.0→0.2→0.0 线性趋黑）")
            if post_idx:
                post_max = max(valid[i][1] for i in post_idx)
                check("P2/撤帧后无二次跳变（+0.3s/+0.6s，vs 撤帧前帧）",
                      post_max <= P2_AFTER_MAX_POINTS,
                      f"撤帧后 {len(post_idx)} 帧最大 {post_max} 点"
                      f"（vs 撤帧前帧；门槛 ≤{P2_AFTER_MAX_POINTS}；"
                      "旧实现同项是暗面→真面板的整块硬跳，量级 >> 门槛）")
            else:
                skip("P2/撤帧后无二次跳变", "没拍到撤帧后稳定性帧")

        # 旁证：面板事件序列（判定不看它，回答"落定路径有没有重新映射/重绘"）
        if taps.events:
            rows = sorted((round((ts - t0) * 1000.0), kind, cls, vis)
                          for ts, kind, cls, vis in taps.events)
            by_kind = {k: sum(1 for r in rows if r[1] == k)
                       for k in ("Show", "Hide", "Paint", "Expose")}
            info("P2/面板事件序列",
                 " / ".join(f"{k} {n} 次" for k, n in by_kind.items())
                 + f"（{rows[0][0]}~{rows[-1][0]}ms）；逐条：")
            print("      t_ms   事件    类名                 visible")
            for t_ms, kind, cls, vis in rows[:24]:
                print(f"    {t_ms:6.0f}  {kind:<6}  {cls:<18}  {vis}")
            if len(rows) > 24:
                print(f"    …（其余 {len(rows) - 24} 条略）")
        else:
            skip("P2/面板事件序列", "没抓到任何 Show/Hide/Paint/Expose")
    finally:
        # 后面的场景要求设置是关着的
        close_settings(app, window)


def _p2_fallback_timer(app, window, dpr: float, taps, t0: float) -> None:
    """兜底：QTimer(0) 连拍（lead 批准的后备方案）。

    单区（落定区）判峰，按钮区降到 grid 16 粗采做对照；窗 760ms、
    判定窗 [520,700]（盖住真实 Show≈559 与落定区活动 560~680ms）。
    """
    # ⚠️ 落定区不能在这里读：_settings_geom_to 点击前是空 QRect，
    # 挪到第一拍（sample_once）时再读（与主路径 _SettingsSwapTap 同理）。
    land: QRect | None = None
    g_land: ScreenGrabber | None = None
    btn = _p2_zone_button(window)
    g_btn = ScreenGrabber(btn, dpr)
    frames: list[tuple[float, int | None, int | None, int]] = []   # (t, 落定区点, 钮区点, 落定区luma)
    state = {"prev_l": None, "prev_lum": 0, "prev_b": None, "prev_blum": 0,
             "on": True}
    timer = QTimer()
    timer.setInterval(0)

    def sample_once() -> None:
        nonlocal g_land, land
        if not state["on"]:
            return
        if g_land is None:
            land = _p2_zone_landing(window)
            if land is not None:
                g_land = ScreenGrabber(land, dpr)
        t = (time.perf_counter() - t0) * 1000.0
        cl: int | None = None
        lum = 0
        if g_land is not None:
            cur_l = g_land.grab()
            lum = _frame_luma(cur_l)            # 黑屏甄别
            # 黑帧后重置 diff 链基线：黑→活转换会被误算成巨量「变化点」
            base_l = state["prev_l"] if state["prev_lum"] > 24 else None
            cl = (None if base_l is None else
                  changed_points(base_l, cur_l, g_land.width, g_land.height))
            state["prev_l"] = cur_l
            state["prev_lum"] = lum
        cur_b = g_btn.grab()
        lum_b = _frame_luma(cur_b)
        base_b = state["prev_b"] if state["prev_blum"] > 24 else None
        cb = (None if base_b is None else
              changed_points(base_b, cur_b, g_btn.width, g_btn.height, grid=16))
        state["prev_b"] = cur_b
        state["prev_blum"] = lum_b
        frames.append((t, cl, cb, lum))
        if t >= P2_CAPTURE_MS:
            state["on"] = False
            timer.stop()

    timer.timeout.connect(sample_once)
    try:
        timer.start()
        window.btn_settings.click()
        while (time.perf_counter() - t0) * 1000.0 < P2_CAPTURE_MS:
            app.processEvents()
            time.sleep(0.001)
    finally:
        timer.stop()
        state["on"] = False
        if g_land is not None:
            g_land.close()
        g_btn.close()
        _app = QApplication.instance()
        if _app is not None:
            _app.removeEventFilter(taps)

    try:
        usable = [(t, cl, cb) for t, cl, cb, lum in frames
                  if cl is not None and lum > 24]     # 黑帧剔除
        n_black = sum(1 for _t, cl, _cb, lum in frames
                      if cl is not None and lum <= 24)
        if len(usable) < 8:
            alive_now, an = _screen_alive()
            skip("P2/换帧序列无孤立像素峰（兜底）",
                 "拍屏期间桌面全黑（黑帧已剔除）" if n_black or not alive_now
                 else f"只采到 {len(usable)} 帧")
            return
        gaps = [b[0] - a[0] for a, b in zip(usable, usable[1:])]
        gap_med = statistics.median(gaps) if gaps else 0.0
        alive2, _a2 = _screen_alive()
        info("P2/连拍（兜底）",
             f"{len(usable)} 有效帧（剔除黑帧 {n_black}）/ {P2_CAPTURE_MS:.0f}ms；"
             f"帧间隔中位 {gap_med:.1f}ms"
             f"（≈{1000.0 / max(0.1, gap_med):.0f}fps，目标 ≥{P2_TARGET_FPS}）；"
             f"拍完复核 {'ALIVE' if alive2 else 'BLACK'}；"
             f"落定区 " + (f"({land.x()},{land.y()} "
                           f"{land.width()}×{land.height()})"
                           if land is not None else "未取到"))
        series = [(t, cl) for t, cl, _cb in usable]
        counts = [c for _t, c in series]
        lo, hi = P2_SETTLE_WINDOW
        spikes = []
        for i, (t, c) in enumerate(series):
            if not (lo <= t <= hi):
                continue
            if i and (t - series[i - 1][0]) > 60.0:
                continue                    # 大间隔帧：差值跨长墙钟，不判峰
            neigh = [counts[j]
                     for j in range(max(0, i - P2_NEIGHBOURS),
                                    min(len(series), i + P2_NEIGHBOURS + 1))
                     if j != i]
            if not neigh:
                continue
            med = statistics.median(neigh)
            if c > max(med * P2_SPIKE_FACTOR, P2_SPIKE_ABS):
                spikes.append((i, t, c, med))
        in_win = [c for t, c in series if lo <= t <= hi]
        if spikes:
            i, t, c, med = max(spikes, key=lambda s: s[2])
            table = "；".join(f"{ft:.0f}ms:落{fc}点"
                              for ft, fc in series[max(0, i - 5):i + 6])
            check(f"P2/落定窗口 {lo:.0f}~{hi:.0f}ms 无孤立像素峰（兜底）", False,
                  f"孤立峰 {len(spikes)} 个；最狠 t={t:.0f}ms 变化 {c} 点"
                  f"（邻帧中位 {med:.0f}）；逐帧 {table}")
        else:
            check(f"P2/落定窗口 {lo:.0f}~{hi:.0f}ms 无孤立像素峰（兜底）", True,
                  f"窗口内 {len(in_win)} 帧无孤立峰；最大 "
                  f"{max(in_win) if in_win else 0} 点")
    finally:
        close_settings(app, window)


# ------------------------------------------------------------------ 场景

def land_settings(app, window) -> bool:
    """打开设置，等到**换帧完成**（快照撤走、真面板上屏）。"""
    if window._settings_dialog is not None:
        window._close_settings_dialog(accepted=False)
        pump_until(app, lambda: window._settings_dialog is None, 1500)
    window.btn_settings.click()

    def landed():
        if window._settings_dialog is None:
            return False
        if window._settings_swap_target is not None:
            return False
        try:
            if window._settings_shot.isVisible():
                return False
        except RuntimeError:
            return False
        return True

    return pump_until(app, landed, 2500)


def close_settings(app, window) -> None:
    if window._settings_dialog is not None:
        window._close_settings_dialog(accepted=False)
        pump_until(app, lambda: window._settings_dialog is None, 2000)
    pump(app, 0.15)


def duration_of(app, window, action) -> float:
    """量一段动画的**墙钟时长**（从 action 到补间全部停下）。"""
    import ui_anim
    pump_until(app, lambda: not ui_anim.ticker_active(), 1000)
    t0 = time.perf_counter()
    action()
    started = False
    deadline = t0 + 2.5
    while time.perf_counter() < deadline:
        app.processEvents()
        active = ui_anim.ticker_active()
        if active:
            started = True
        elif started:
            break
        time.sleep(0.001)
    return (time.perf_counter() - t0) * 1000.0


def _cum_check(events, qs) -> dict:
    """**累积口径自检**（lead + 工程师 2026-10-18 批准的结案判据）。

    为什么不用"逐帧步长 vs 1.00±0.05"：`_rx_place_switch` 里是
    ``int(round(...))``，**每帧自带 ±0.5px 取整误差**，而单帧位移才 2~6px ⇒
    逐帧比值自带 ±8~24% 噪声，"1.00±0.05"物理不可达，一定会误判成时间线问题。
    累积位移把取整摊薄到 ±0.2%，残差（px）才是可判的量。

    两个坑同时在桶里：① span 必须取 **t≈1** 处的首末距离，取短了残差会假装
    "单调漂移"；② 用**过原点最小二乘**的 span_fit 重算，天然免疫这个坑。

    返回 dict：span_end / span_fit / 比值 / max|res| 及其位置 / 形状 /
    相对偏差 / res@10% 档 —— 形状三分类对应三个机制：
      随机抖 ±1px（纯取整，结案）、
      弓形（中段鼓，= 展开方向"移动靶"follow-up 的实测证据）、
      台阶（单点 >2px 跳，= 某帧 _rx_t 被写两次，lead 定的 highest 真 bug）。
    """
    if len(events) < 4:
        return {}
    x0, y0 = events[0][1][0], events[0][1][1]
    q0 = qs[0]
    cpos, dqs = [], []
    for (_t, s), q in zip(events, qs):
        # ⚠️ 取绝对值：退出动画的 q 是 1→0 **递减**，直接相减会让拟合斜率变负
        #    （第一轮实测 span_fit=-206.5px 就是这么来的）
        cpos.append(math.hypot(s[0] - x0, s[1] - y0))
        dqs.append(abs(q - q0))
    span_end = cpos[-1]
    den = sum(d * d for d in dqs)
    span_fit = (sum(c * d for c, d in zip(cpos, dqs)) / den
                if den > 1e-12 else 0.0)
    res = [c - span_fit * d for c, d in zip(cpos, dqs)]
    worst_i = max(range(len(res)), key=lambda i: abs(res[i]))
    worst = abs(res[worst_i])
    # 相对偏差只在 q 够大处算（q≈0 时分子被放大无意义）
    rel = [abs(r) / max(1e-6, span_fit * d) for r, d in zip(res, dqs)
           if d >= 0.05]
    max_rel = max(rel, default=0.0)
    q_end = max(dqs) or 1e-9
    x0, xe = events[0][1][0], events[-1][1][0]
    y0, ye = events[0][1][1], events[-1][1][1]
    xspan = (xe - x0) or 1e-9
    yspan = (ye - y0) or 1e-9
    deciles = []
    for k in range(0, 11):
        hit = next((i for i, d in enumerate(dqs) if d >= q_end * k / 10.0), None)
        if hit is not None:
            s = events[hit][1]
            deciles.append(f"{k * 10}%=({(s[0] - x0) / xspan * 100:.0f}%x,"
                           f"{(s[1] - y0) / yspan * 100:.0f}%y)@{res[hit]:+.1f}px")
    # 真台阶是**孤立尖峰**：二阶差分（相邻三点）大于 2px；只看一阶差分会被
    # 稀疏采样的正常爬升误触发（实测 8~12 个事件覆盖 214px，相邻残差本就
    # 有 10px 量级的正常增量）。
    spike = max((abs(res[i] - 2 * res[i - 1] + res[i - 2])
                 for i in range(2, len(res))), default=0.0)
    if spike > 2.0:
        shape = f"台阶（孤立尖峰 {spike:.1f}px，疑 _rx_t 单帧写两次）"
    elif abs(res[0]) <= 1.0 and abs(res[-1]) <= 1.0 and 0.2 <= dqs[worst_i] / q_end <= 0.8:
        shape = "弓形（中段鼓，=移动靶）"
    elif worst > 1.0:
        shape = ("光滑偏离（非仿射：x/y 推进不同步或父布局叠加，"
                 f"孤立尖峰 {spike:.1f}px）")
    else:
        shape = "随机抖动（纯取整）"
    return {"span_end": span_end, "span_fit": span_fit,
            "ratio": (span_end / span_fit) if span_fit > 1e-9 else 0.0,
            "worst": worst, "worst_q": dqs[worst_i], "shape": shape,
            "max_rel": max_rel, "deciles": " ".join(deciles)}


def analyse_curve_normalised(rows, duration_ms: float):
    """G′（**正式判据**，lead 2026-10-19 终裁）：跟踪斜率对拍 1.0×行程。

    公式（写进总表可直接引用）::

        slope = Δgeo / |Δq|            # 欧氏距离；Δgeo 与 Δq(window._rx_t) 同拍成对读
        参照   = 1.0 × 行程（完美跟踪；`_rx_t` 是曲线后进度，几何对它应线性）
        判定   = max(slope) ≤ 1.4×行程 且 ΣΔgeo/ΣΔq ≤ 1.2×行程
                 （多事件布局类控件——功能列/工具栏行——均值门槛 1.25×）
                 max 只统计 Δgeo>4px（可见性下限，lead 批准）且 |Δq|≥0.01 的对
                 （取整/无推进毛刺，明细照印；再抬地板必须过 lead）
        第二道闸 = 绝对单步 ≤ 1.4×行程×(16ms/260ms)，与 Δq 无关 —— 堵 Δq 地板
                 的盲区（Δq≈0.004 的真跳会被地板放过，绝对单步照样抓）
        硬凭证 = Δq==0 且 Δgeo>4px 的对 == 0（结构跳变）；
                 Δq==0 且 Δgeo≤4px 记「收尾 settle」，单独计数不算 FAIL

    为什么参照不是 4.04×行程/duration（曲线前理论峰值）：`_rx_t` 是**曲线后**
    进度，`Δq × duration ≠ 真实墙钟`，分子分母不同钟 —— 旧参照下的 0.30×
    是虚余量。跟踪斜率口径下墙钟采样相位差被结构性约掉（旧墙钟 dt 版本
    1.44~1.58× 的超限全部消失）。

    返回 ``(short, ok_slope | None, detail, ok_struct, detail_struct,
    ok_step, detail_step, dev_ok)``；``ok_slope is None`` = 基本没动 / 行程过小；
    ``dev_ok`` = 满足已知偏差单列护栏（见尾部注释）。
    """
    min_dt = _motion_min_dt()
    out = []
    for short, _attr in TRACK:
        pairs, dq_zero, span, cum_events, cum_qs = _widget_motion_q(
            rows, short, min_dt)
        settle = [e[2] for e in dq_zero if e[2] <= TRACK_SETTLE_MAX_PX]
        struct = [e for e in dq_zero if e[2] > TRACK_SETTLE_MAX_PX]
        ok_struct = not struct
        struct_detail = (f"Δq==0 结构跳变(>4px) {len(struct)} 条"
                         + ("（" + "；".join(
                             f"Δgeo={e[2]:.0f}px[Δpos={e[3]:.0f}/Δsize={e[4]:.0f}"
                             f"@{e[0]:.0f}→{e[1]:.0f}ms,q={e[5]:.3f}→{e[6]:.3f}]"
                             for e in struct[:3]) + "）" if struct else "")
                         + f"；收尾 settle(≤4px) {len(settle)} 条")
        # 绝对单步（与 Δq 无关，堵 max 地板的盲区）：只看 Δgeo 本身。
        # 门槛含 CURVE_PEAK：诚实曲线在最陡步的位移 = 行程×4.04×(16/260)。
        step_tol = (TRACK_STEP_TOL * span * TRACK_STEP_CURVE_FACTOR
                    * (THROTTLE_STEP_MS / max(1.0, duration_ms)))
        max_step = max((p[0] for p in pairs), default=0.0)
        if span >= 8:
            ok_step = max_step <= step_tol
            step_detail = (f"最大单步 {max_step:.0f}px / 门槛 {step_tol:.0f}px"
                           f"（{TRACK_STEP_TOL:g}×行程×曲线峰斜率"
                           f"{TRACK_STEP_CURVE_FACTOR:.2f}×{THROTTLE_STEP_MS:.0f}/"
                           f"{duration_ms:.0f}，行程 {span:.0f}px）")
        else:
            ok_step = True
            step_detail = f"行程仅 {span:.0f}px（绝对单步不判定）"
        if not pairs and not dq_zero:
            out.append((short, None, "基本没动（无需判定）", True, "",
                        True, step_detail, False))
            continue
        if span < 8 and not struct:
            out.append((short, None,
                        f"行程仅 {span:.0f}px（不判定）；{struct_detail}",
                        ok_struct, struct_detail, ok_step, step_detail, False))
            continue
        small = [(p[0], p[1]) for p in pairs
                 if p[0] <= TRACK_MAX_MIN_GEO_PX or p[1] < TRACK_MAX_MIN_DQ]
        big = [p for p in pairs
               if p[0] > TRACK_MAX_MIN_GEO_PX and p[1] >= TRACK_MAX_MIN_DQ]
        max_slope = max((p[0] / p[1] for p in big), default=0.0)
        max_pair = max(big, key=lambda p: p[0] / p[1], default=None)
        # 邻居形状判别（工程师 2026-10-19）：max 对的左右邻居 Δq 若远小于它
        # （0.002 级 vs 0.011）⇒ 掉帧/合并帧（几拍的进度挤进一步，几何无辜）；
        # 邻居 Δq 与它同级 ⇒ Δq 本身在那段变大，才可能是曲线/时间线问题。
        nb_txt = ""
        if max_pair is not None:
            try:
                k = pairs.index(max_pair)
            except ValueError:
                k = -1
            nbs = []
            for j in (k - 1, k + 1):
                if 0 <= j < len(pairs):
                    p2 = pairs[j]
                    nbs.append(f"{'前' if j < k else '后'}邻 Δgeo={p2[0]:.1f}px/"
                               f"Δq={p2[1]:.3f}/步长{max(0.1, (p2[3] - p2[2]) * 1000.0):.0f}ms")
            nb_txt = f"；邻居[{ '；'.join(nbs) }]" if nbs else ""
            # 分母同源自检（工程师 2026-10-19 同源重算令）：理论上 Δgeo ≡
            # span×Δq（p≡_rx_t 纯 lerp 恒等式）。拆 Δpos/Δsize——若 max 对的
            # Δsize ≫ span×Δq，则是尺寸被布局带动污染 Δgeo（max(Δpos,Δsize)
            # 的副作用），修法是把位置型控件改只看 Δpos；若 Δpos 本身就超，
            # 才是时间线/父子布局叠加问题。
            _d, _q, _t1, _t2, dpos, dsize = max_pair
            nb_txt += (f"；分解 Δpos={dpos:.1f}/Δsize={dsize:.1f}px vs "
                       f"span×Δq={span * _q:.1f}px")
        sum_dq = sum(p[1] for p in pairs)
        mean_slope = (sum(p[0] for p in pairs) / sum_dq) if sum_dq > 1e-9 else 0.0
        mean_tol = (TRACK_SLOPE_MEAN_TOL_LAYOUT if short in _TRACK_LAYOUT_WIDGETS
                    else TRACK_SLOPE_MEAN_TOL)
        # 首末事件上下文（诊断 span 收缩用）：span 只在首末同为可见时才算，
        # 首事件被推迟捕捉（退出第一帧同步 show 之后才开录）会缩小 span、
        # 把均值斜率抬高 —— 打印可见性与时刻即可判别是不是这个原因。
        ends_txt = ""
        if len(cum_events) >= 2 and rows:
            _t0 = rows[0][0]
            e0, e1 = cum_events[0], cum_events[-1]
            ends_txt = (f"；首末事件 可见{e0[1][4]}→{e1[1][4]}"
                        f"@{(e0[0] - _t0) * 1000.0:.0f}→"
                        f"{(e1[0] - _t0) * 1000.0:.0f}ms"
                        f"/q={cum_qs[0]:.3f}→{cum_qs[-1]:.3f}")
        ok_slope = (max_slope <= TRACK_SLOPE_MAX_TOL * span
                    and mean_slope <= mean_tol * span)
        small_txt = ""
        if small:
            shown = "；".join(f"Δgeo={d:.1f}px/Δq={q:.3f}" for d, q in small[:6])
            more = f" 等共 {len(small)} 对" if len(small) > 6 else ""
            small_txt = (f"；max 剔除 ≤{TRACK_MAX_MIN_GEO_PX:g}px（可见性下限）"
                         f"或 Δq<{TRACK_MAX_MIN_DQ:g} 的对 {shown}{more}")
        # ── 累积口径结案判据（lead + 工程师 2026-10-18）：逐步长比值受
        #    ±0.5px 取整支配，改在**累积位移**上判一组同源指标：
        #    span_end/span_fit 应互证（差 >0.5% = span 取错，不是产品问题），
        #    相对偏差 ≤0.05 且形状不是"台阶" ⇒ 结案，整条 PASS、不写偏差条目。
        cum = _cum_check(cum_events, cum_qs)
        cum_txt = ""
        cum_ok = cum_bad = False
        if cum:
            cum_txt = (f"；累积口径 span_end={cum['span_end']:.1f}/"
                       f"span_fit={cum['span_fit']:.1f}"
                       f"（互证比值 {cum['ratio']:.4f}）；max|res|="
                       f"{cum['worst']:.1f}px @q={cum['worst_q']:.2f}"
                       f"（{cum['shape']}）；相对偏差 max={cum['max_rel']:.3f}；"
                       f"res@10%档 {cum['deciles']}")
            if abs(cum["ratio"] - 1.0) <= 0.005:      # span 取值自检通过
                if cum["shape"].startswith("台阶"):
                    cum_bad = True
                elif cum["max_rel"] <= 0.05:
                    cum_ok = True
        if cum_ok:
            ok_slope = True                            # 结案：不再以逐步长 FAIL
        elif cum_bad:
            # 台阶 = lead 定的 highest priority 真 bug 候选：即使逐步长判据侥幸
            # 过关也要压成 FAIL，并单独上报工程师。
            ok_slope = False
            cum_txt += "；⚠️ 累积残差出现单点台阶，按 lead 口径压成 FAIL 上报"
        # PASS(dev) 护栏（lead 2026-10-19 终裁）：串口开关 max 斜率单列。
        # ① 均值达标 ② 绝对单步达标 ③ 结构跳变 0 ④ max 斜率 ≤2.0×行程（掉帧
        # 上限：一帧吃两拍 = 2.0，超它就不是合并帧）且 max 对 ≤5.2px（可见性
        # 上界，占行程 ≤2.4%）。地板停 4px，再上爬必须过 lead。
        dev_ok = bool(
            short in _TRACK_KNOWN_DEV and not ok_slope
            and ok_struct and ok_step
            and mean_slope <= mean_tol * span
            and max_slope <= 2.0 * span
            and (max_pair is None or max_pair[0] <= 5.2))
        out.append((short, ok_slope,
                    f"最大斜率 {max_slope / span:.2f}×行程 / 均值 "
                    f"{mean_slope / span:.2f}×行程（门槛 {TRACK_SLOPE_MAX_TOL:g}/"
                    f"{mean_tol:g}，行程 {span:.0f}px）"
                    + (f"；max 对 Δgeo={max_pair[0]:.1f}px/Δq={max_pair[1]:.3f}"
                       f"（占行程 {max_pair[0] / span * 100:.1f}%）"
                       if max_pair else "")
            + nb_txt
            + cum_txt
            + ends_txt
            + f"{small_txt}；{struct_detail}",
            ok_struct, struct_detail, ok_step, step_detail, dev_ok))
    return out


def _report_geometry(app, window, rows, label: str) -> None:
    duration_ms = float(getattr(window, "_RX_TRANSITION_MS", 260))
    span = (rows[-1][0] - rows[0][0]) * 1000 if len(rows) > 1 else 0
    fps = 1000 * (len(rows) - 1) / max(1.0, span) if span else 0
    info(f"G/{label}", f"帧数 {len(rows)}（{span:.0f}ms，≈{fps:.0f}fps）；"
         f"事件分组阈值 {_motion_min_dt():.1f}ms；"
         f"dpr={window.devicePixelRatioF():g}"
         f"（几何=Qt 逻辑 px：mapToGlobal/geometry 同源，无物理换算）；"
         f"G′ 公式 slope=Δgeo/Δq（欧氏，Δgeo 与 Δq=_rx_t 同拍成对读，"
         f"参照 1.0×行程；duration={duration_ms:.0f}ms 仅作换算）")
    for short, _ok, detail, _ratio in analyse_geometry(rows):
        info(f"G/{label}·{short} max/median（已作废，仅对照）", detail)
    for (short, ok_slope, detail, ok_struct, _ds,
         ok_step, step_detail, dev_ok) in analyse_curve_normalised(rows, duration_ms):
        name = (f"G'/{label}·{short} 跟踪斜率 max≤{TRACK_SLOPE_MAX_TOL:g}×行程"
                f"（均值≤{TRACK_SLOPE_MEAN_TOL:g}，布局类≤"
                f"{TRACK_SLOPE_MEAN_TOL_LAYOUT:g}）")
        if ok_slope is None:
            check(name, True, detail)
        elif dev_ok:
            # lead 2026-10-19 终裁：串口开关 max 斜率 = 已知偏差，PASS(dev)
            # 单列、不进 FAIL 分母。护栏（均值/单步/结构/2.0×上限/≤5.2px）
            # 全过才会走到这里；措辞照 lead 原文。
            check(name + "（已知偏差·单列）", True,
                  "「串口开关峰值跟踪斜率 1.62~1.76×（门槛 1.4），由 ≤5px 的"
                  "取整对贡献（占行程 ≤2.4%）；均值 1.06、单步余量 16%、"
                  "结构跳变 0，均达标】实测：" + detail)
        else:
            check(name, ok_slope, detail)
        check(f"G'/{label}·{short} 绝对单步 ≤{TRACK_STEP_TOL:g}×行程×"
              f"{THROTTLE_STEP_MS:.0f}/{duration_ms:.0f}（结构跳变第二道闸，与 Δq 无关）",
              ok_step, step_detail)
        check(f"G'/{label}·{short} 无 Δq==0 二次落地>4px（结构跳变硬判据）",
              ok_struct, _ds)


def scenario_expand(app, window, tap, label: str) -> None:
    if window._expanded:
        window._collapse_receive()
        pump(app, 1.0)
    _report_geometry(app, window, tap.run(window._expand_receive), label)


def scenario_collapse(app, window, tap, label: str) -> None:
    if not window._expanded:
        window._expand_receive()
        pump(app, 1.2)
    _report_geometry(app, window, tap.run(window._collapse_receive), label)


def _merge_series(series, min_dt_ms: float = MIN_FRAME_MS):
    """把间隔 < min_dt_ms 的相邻采样合并成一帧（同 GeometryTap 的理由）。"""
    if len(series) < 2:
        return list(series)
    out = [series[0]]
    for t, v in series[1:]:
        if (t - out[-1][0]) * 1000.0 >= min_dt_ms:
            out.append((t, v))
        else:
            out[-1] = (out[-1][0], v)
    return out


def _tap_progress(app, window):
    """返回 (series, on_tick)。**不**连 timer —— 由调用方在 ``_tick`` 里调一次，
    否则 timer.timeout 和 _tick 会各调一次、每帧记两遍。"""
    series: list[tuple[float, float]] = []

    def on_tick() -> None:
        try:
            series.append((time.perf_counter(), float(window._rx_t)))
        except RuntimeError:
            pass

    return series, on_tick


def _run_transition(app, action, max_ms: float = 2500.0) -> None:
    import ui_anim
    action()
    pump_until(app, lambda: not ui_anim.ticker_active(), max_ms)


def _direction_check(series) -> tuple[bool, bool, str]:
    """折返点前后的方向与幅度。返回 (方向相反?, 幅度达标?, 说明)。"""
    merged = _merge_series(series)
    vals = [v for _t, v in merged]
    if len(vals) < 3:
        return False, False, f"样本不足（{len(vals)}）"
    i_turn = max(range(len(vals)), key=lambda i: vals[i])   # 展开中断 → 最大点折返
    d_prev = None
    for i in range(i_turn, 0, -1):
        delta = vals[i] - vals[i - 1]
        if delta != 0:
            d_prev = delta
            break
    d_next = None
    for i in range(i_turn, len(vals) - 1):
        delta = vals[i + 1] - vals[i]
        if delta != 0:
            d_next = delta
            break
    if d_prev is None or d_next is None:
        return False, False, f"折返点无位移（{len(vals)} 点）"
    dir_ok = (d_prev > 0 > d_next) or (d_prev < 0 < d_next)
    amp_ok = abs(d_next) <= max(0.005, abs(d_prev) * INTERRUPT_AMP_MAX)
    return dir_ok, amp_ok, (f"上一帧 Δ={d_prev:+.3f}，反向首帧 Δ={d_next:+.3f}"
                            f"（允许 {abs(d_prev) * INTERRUPT_AMP_MAX:.3f}）")


def scenario_interrupts(app, window) -> None:
    """在 20% / 50% / 80% 三处打断 + 反方向（收起途中打断）各验一次。

    每组验三件事：①反向首帧方向相反且幅度 ≤1.5× ②最终状态正确 ③无残留补间。
    """
    import ui_anim
    cases = [(0.20, False), (0.50, False), (0.80, False), (0.50, True)]
    for frac, start_expanded in cases:
        tag = f"{'收起' if start_expanded else '展开'}途中 @{int(frac * 100)}%"
        # 起点摆正
        if start_expanded:
            if not window._expanded:
                _run_transition(app, window._expand_receive)
            pump(app, 0.25)
        else:
            if window._expanded:
                _run_transition(app, window._collapse_receive)
            pump(app, 0.25)

        series, on_tick = _tap_progress(app, window)
        state = {"rev": False}
        from ui_anim import _ticker

        def rev_check():
            if state["rev"]:
                return
            t = float(window._rx_t)
            if (not start_expanded and t >= frac) or (start_expanded and t <= 1.0 - frac):
                state["at"] = t                     # 反向**当时**的进度（别在收尾后再读）
                if start_expanded:
                    window._expand_receive()
                else:
                    window._collapse_receive()
                state["rev"] = True

        # rev_check 也要按 tick 节奏跑：挂到同一个 _tick 之后
        orig_tick = _ticker()._tick

        def patched():
            orig_tick()
            try:
                on_tick()
                rev_check()
            except RuntimeError:
                pass

        _ticker()._tick = patched
        try:
            if start_expanded:
                window._collapse_receive()
            else:
                window._expand_receive()
            def settled() -> bool:
                tween = getattr(window, "_rx_tween", None)
                return state["rev"] and (tween is None or not tween.running)

            pump_until(app, settled, 2500)
            pump(app, 0.2)
        finally:
            _ticker()._tick = orig_tick

        check(f"I/{tag} 确实触发了反向", state["rev"],
              f"反向**当时** _rx_t={state.get('at', float('nan')):.3f}"
              f"（目标 {frac:.2f}）")
        if start_expanded:
            dir_ok, amp_ok, detail = _direction_check(
                [(t, 1.0 - v) for t, v in series])       # 收起时进度递减 → 取补看折返
        else:
            dir_ok, amp_ok, detail = _direction_check(series)
        check(f"I/{tag} 反向首帧方向相反", dir_ok, detail)
        check(f"I/{tag} 反向首帧幅度 ≤ 上一帧×{INTERRUPT_AMP_MAX:g}", amp_ok, detail)

        # ② 最终状态
        expect_expanded = start_expanded
        final_t = float(window._rx_t)
        state_ok = (window._expanded == expect_expanded
                    and abs(final_t - (1.0 if expect_expanded else 0.0)) <= 0.02)
        check(f"I/{tag} 最终状态正确",
              state_ok, f"_expanded={window._expanded} _rx_t={final_t:.3f} "
                        f"（期望 expanded={expect_expanded}）")
        # ③ 无残留补间：断言**转场补间本身**。ticker_active() 是"共享计时器在跑"，
        #    接收区时钟 FlashLabel（380ms）/ 卡片描边闪都会点亮它，与转场无关
        #    （工程师 5 组里 3 False / 2 True，正是时钟闪占空比）—— 只作 INFO。
        tween = getattr(window, "_rx_tween", None)
        running = bool(tween.running) if tween is not None else False
        check(f"I/{tag} 无残留补间（_rx_tween.running）", not running,
              f"_rx_tween.running={running}")
        info(f"I/{tag} ticker_active（不判定）",
             f"{ui_anim.ticker_active()}（共享计时器，含时钟闪占空比）")
        # 复位到普通态，进入下一组
        if window._expanded:
            _run_transition(app, window._collapse_receive)
        pump(app, 0.3)


def scenario_progress_attribution(app, window) -> None:
    """用**应用自己的进度值**判断"追帧跳"是逻辑问题还是帧耗时。

    做法：包住 ``_rx_tween._on_value``（它拿到的就是 ``_on_rx_transition`` 的入参 t），
    逐帧记 ``(perf_counter, t, tween._begin, _from, _to, duration)``，然后
    用 ``tween._begin`` 反算"这一帧按 STANDARD 应当走到哪"，与实际 t 比。
    """
    from ui_anim import STANDARD

    # 先把 tween 建出来（懒创建）
    if window._expanded:
        _run_transition(app, window._collapse_receive)
    if getattr(window, "_rx_tween", None) is None:
        _run_transition(app, window._expand_receive)
        _run_transition(app, window._collapse_receive)
        pump(app, 0.2)
    tween = window._rx_tween
    if tween is None:
        check("A/进度归因：拿到 _rx_tween", False, "tween 未创建")
        return

    records: list[tuple] = []
    orig_cb = tween._on_value

    def cb(value):
        records.append((time.perf_counter(), float(value),
                        float(getattr(tween, "_begin", 0.0)),
                        float(getattr(tween, "_from", 0.0)),
                        float(getattr(tween, "_to", 0.0)),
                        float(getattr(tween, "duration", 260.0))))
        orig_cb(value)

    tween._on_value = cb
    try:
        window._expand_receive()
        # 等**这个补间**停（timer.isActive 会被时钟闪等无关动画点亮）
        pump_until(app, lambda: tween is not None and not tween.running, 2500)
        pump(app, 0.1)
    finally:
        tween._on_value = orig_cb

    if len(records) < 4:
        check("A/进度归因：采到足够帧", False, f"只有 {len(records)} 帧")
        return

    def clamp01(x: float) -> float:
        return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)

    worst = (0.0, 0.0, 0.0, 0)          # dv, dt_ms, expected_dv, index
    ratios: list[tuple[float, float, float]] = []
    restarts = 0
    for i, (a, b) in enumerate(zip(records, records[1:])):
        dt = b[0] - a[0]
        if dt <= 0:
            continue
        dv = b[1] - a[1]
        if abs(b[2] - a[2]) > 1e-9:
            restarts += 1                # _begin 变了 = 补间被重启
            continue
        dur = max(1.0, a[5]) / 1000.0
        frm, to = a[3], a[4]
        u1 = clamp01((a[0] - a[2]) / dur)
        u2 = clamp01((b[0] - a[2]) / dur)
        exp_dv = (to - frm) * (STANDARD(u2) - STANDARD(u1))
        if abs(dv) > abs(worst[0]):
            worst = (dv, dt * 1000.0, exp_dv, i)
        if abs(exp_dv) >= 0.01:
            ratios.append((abs(dv) / abs(exp_dv), dv, exp_dv))

    ratios.sort(reverse=True)
    over2 = [r for r in ratios if r[0] > 2.0]
    worst_ratio = ratios[0][0] if ratios else 0.0
    check("A/进度增量 ≤ 时间推进应得值的 2×（逻辑是否诚实）",
          not over2,
          f"最大比值 {worst_ratio:.2f}（判据 ≤2）；超 2× 的帧 {len(over2)}/{len(ratios)}；"
          f"补间重启 {restarts} 次")
    check("A/单帧最大进度增量 与该帧理论应得值吻合（≤2×）",
          abs(worst[0]) <= abs(worst[2]) * 2.0 + 0.02,
          f"实测 Δ进度={worst[0]:+.3f} / 用时 {worst[1]:.1f}ms / 理论 Δ={worst[2]:+.3f}")
    # 工程师报的"0.07→0.39 @22ms"是否说得通 —— 直接按曲线算
    info("A/工程师的 0.07→0.39 @22ms 是否可能",
          f"22ms 的理论最大 Δ={CURVE_PEAK * 22.0 / 260.0:.3f}"
          f"（曲线峰值斜率 {CURVE_PEAK:.2f}），故 0.32 属时间推进应得值，"
          f"不是补间回绕")
    info("A/STANDARD 理论最大斜率", f"{CURVE_PEAK:.2f}（Δ进度/Δ归一化时间）；"
         f"8ms 帧理论最大 Δ={CURVE_PEAK * 8.0 / 260.0:.4f}")


# ============================================================== S1 / S2 / S3
# 追加判据（2026-10-18 晚）。运行方式：
#     python devtools\_qa_anim_verdict.py        # 原有 G/P/I/T（main）
#     python devtools\_qa_anim_verdict.py S      # 只跑 S1+S2+S3
#     python devtools\_qa_anim_verdict.py S1     # 只跑 S1（S2/S3 同理）
# 每段都是 (app, window) -> None：自己取样、自己打印实测值，**失败不抛异常**。

_SKIPPED: list[tuple[str, str]] = []


def skip(name: str, detail: str = "") -> None:
    """判据**无法验证**（接口未定 / 前置条件不成立）。

    单独记录、不混进 PASS/FAIL 分母 —— 否则"没有这个接口"会被算成"实现错了"。
    """
    _SKIPPED.append((name, detail))
    print(f"SKIP  {name}" + (f"   [{detail}]" if detail else ""))


class FrameTap:
    """通用逐帧取样：挂在 ``ui_anim._Ticker._tick`` 之后，取样内容由调用方给。

    为什么不复用 ``GeometryTap``：那个把 ``TRACK`` 里那 8 个控件的
    ``(x, y, w, h, visible)`` 绑死了；S1/S2 要的是"标签宽 / 卡宽 / 交集面积"
    这类派生量，而且必须**与 ``_rx_t`` 同帧**读到才有意义。
    """

    def __init__(self, sampler) -> None:
        self.sampler = sampler
        self.rows: list[tuple[float, dict]] = []
        self._recording = False
        self._orig = None
        self._ticker = None

    def __enter__(self):
        import ui_anim
        self._ticker = ui_anim._ticker()
        self._orig = self._ticker._tick

        def patched():
            self._orig()
            if self._recording:
                try:
                    snap = self.sampler()
                except RuntimeError:            # 窗口收尾，控件已析构
                    snap = None
                if snap is not None:
                    self.rows.append((time.perf_counter(), snap))

        self._ticker._tick = patched
        return self

    def __exit__(self, *_exc):
        if self._ticker is not None and self._orig is not None:
            self._ticker._tick = self._orig
        return False

    def run(self, action, settle_ms: float = 2500.0) -> list[tuple[float, dict]]:
        import ui_anim
        self.rows = []
        self._recording = True
        t0 = time.perf_counter()
        action()
        deadline = t0 + settle_ms / 1000.0
        started = False
        while time.perf_counter() < deadline:
            self.app_pump()
            active = ui_anim.ticker_active()
            started = started or active
            if started and not active:
                break
        self._recording = False
        return self.rows

    def app_pump(self) -> None:
        """泵一次事件循环（不 sleep：``GeometryTap.run`` 里那 1ms 会吃掉首帧）。"""
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.processEvents()


def _rect_of(widget) -> QRect:
    """控件在**屏幕坐标**下的矩形（跨父级比较用，父级不同也不影响）。"""
    return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())


def _switch_sample(window) -> dict | None:
    """S1/S2 共用取样：时间线进度 + 标签/卡/列宽度 + 开关与 cmb_link 的矩形。

    依赖控件（全部是 ``MainWindow`` 的属性，**不改它们，只读**）：
      ``_rx_t``        时间线进度（0=普通态，1=最大化态）
      ``_port_label``  串口选择标签（在配置卡里；改完之后卡内应有定宽内容容器）
      ``_config_card`` 配置卡（当前每帧 setFixedWidth，目标：纯裁切）
      ``_open_row``    串口开关整行（转场期间被 reparent 到窗口上飞行）
      ``cmb_link``     卡内第一个表单控件（"连接方式"下拉）—— S2-2 的对照物
      ``_rx_rail``     功能列（开关最终飞进去的那一列，只作诊断打印）
    """
    try:
        row = window._open_row
        card = window._config_card
        label = window._port_label
        rail = window._rx_rail
        link = window.cmb_link            # 卡里第一个表单控件（"连接方式"下拉）
        row_rect = _rect_of(row)
        card_rect = _rect_of(card)
        font = label.font()
        weight = font.weight()
        return {
            "t": float(getattr(window, "_rx_t", 0.0)),
            "row": row_rect,
            "row_vis": bool(row.isVisible()),
            "card": card_rect,
            "card_vis": bool(card.isVisible()),
            "card_w": int(card.width()),
            "link": _rect_of(link),
            "rail": _rect_of(rail),
            "rail_w": int(rail.width()),
            "label_w": int(label.width()),
            "label_hint": int(label.sizeHint().width()),
            "label_font": (font.family(),
                           round(float(font.pointSizeF()), 2),
                           int(getattr(weight, "value", weight))),
        }
    except RuntimeError:                        # 窗口收尾，控件已析构
        return None


# ------------------------------------------------------------------- S1 字体

def scenario_s1_font(app, window) -> None:
    """S1「字体不干涉」：退出最大化全程，串口标签的**字形量**不许变。

    前提（工程师本轮改动）：配置卡内换成**定宽内容容器** `_config_content`
    （构造时 setFixedWidth(卡宽-边距)，转场期间不变），卡片收缩改为**纯裁切**。
    改完之后 `_port_label.width()` 就必须恒定 —— 这是"字体不干涉"的唯一硬证据。
    所以字面判据**保留、不放宽**：现在跑 FAIL 只说明"改动还没生效"，不是判据错。

    五条（缺一不可）：
      ① ``_port_label.width()`` 整段 max-min == 0
      ② ``_port_label.sizeHint().width()`` 整段不变（字体量，与布局无关）
      ③ ``QFont`` 签名（family / pointSizeF / weight）整段不变
      ④ ``_config_card.width()`` 退出全程单调不减
      ⑤ 没有"Δt=0 但几何变了"的帧（布局攒帧后一次跳的证据）
    """
    if not window._expanded:
        _run_transition(app, window._expand_receive)
    pump(app, 0.4)
    if not window._expanded:
        skip("S1/退出最大化取样", "没能先进入最大化态（_expand_receive 未生效）")
        return

    with FrameTap(lambda: _switch_sample(window)) as tap:
        rows = tap.run(window._collapse_receive, settle_ms=2500)

    merged = _merge_subframes(rows)
    if len(merged) < 4:
        skip("S1/退出最大化取样", f"只采到 {len(merged)} 帧（动画没跑？）")
        return

    snaps = [s for _t, s in merged]
    widths = [s["label_w"] for s in snaps]
    hints = [s["label_hint"] for s in snaps]
    cards = [s["card_w"] for s in snaps]
    fonts = {s["label_font"] for s in snaps}
    span_ms = (merged[-1][0] - merged[0][0]) * 1000.0
    info("S1/取样",
         f"{len(merged)} 帧 / {span_ms:.0f}ms；label_w {min(widths)}~{max(widths)}；"
         f"sizeHint {min(hints)}~{max(hints)}；card_w {min(cards)}~{max(cards)}；"
         f"p 起止 {snaps[0]['t']:.2f}→{snaps[-1]['t']:.2f}")

    drift = max(widths) - min(widths)
    check("S1/_port_label.width() 整段 max-min == 0（字面判据，硬指标）",
          drift == 0,
          f"实测 {min(widths)}~{max(widths)}（差 {drift}px）；"
          "FAIL 即说明定宽内容容器还没生效（卡宽在动，标签跟着动）")

    check("S1/_port_label.sizeHint().width() 整段不变（字体量硬指标）",
          max(hints) - min(hints) == 0, f"实测 {min(hints)}~{max(hints)}")
    check("S1/_port_label 字体签名整段不变",
          len(fonts) == 1, f"出现 {len(fonts)} 种：{sorted(fonts)}")

    drops = []
    for (_t1, a), (_t2, b) in zip(merged, merged[1:]):
        if a["card_vis"] and b["card_vis"] and b["card_w"] < a["card_w"] - 1:
            drops.append((a["t"], b["card_w"] - a["card_w"]))
    detail = ("全程单调" if not drops else
              f"回退 {len(drops)} 次，最差 {min(d[1] for d in drops)}px @p={drops[0][0]:.2f}")
    check("S1/_config_card.width() 退出全程单调不减", not drops, detail)

    stuck = []
    for (_t1, a), (_t2, b) in zip(merged, merged[1:]):
        if abs(b["t"] - a["t"]) > 1e-9:
            continue
        d_card = abs(b["card_w"] - a["card_w"])
        d_label = abs(b["label_w"] - a["label_w"])
        if d_card > 1 or d_label > 1:
            stuck.append((a["t"], d_card, d_label))
    detail = ("没有" if not stuck else
              f"{len(stuck)} 帧，最差 Δ卡={max(s[1] for s in stuck)}px / "
              f"Δ标={max(s[2] for s in stuck)}px @p={stuck[0][0]:.2f}")
    check("S1/没有「Δt=0 但几何变了」的帧", not stuck, detail)


# --------------------------------------------------- S2 提前预留开关位

def _bucket_line(snaps) -> str:
    """按 p 取最近帧，给出「卡宽 / 开关 x / 列宽」剖面，一眼看出谁先动。"""
    parts = []
    for mark in (0.05, 0.2, 0.4, 0.6, 0.8, 1.0):
        near = min(snaps, key=lambda s: abs(s["t"] - mark))
        parts.append(f"p{mark:.2f}:卡{near['card_w']}px 开关@{near['row'].x()}px")
    return "  ".join(parts)


def _s2_analyse(window, label: str, merged) -> None:
    """对一段（展开或退出）的逐帧样本做 S2 的三条断言。

    为什么**不**判"开关与整张卡零交集"：开关的落位点 ``_open_row_home`` 就是卡内
    ``col`` 布局的顶部，终点在卡里面 —— 零交集物理上不可能。真正要保证的是
    "卡先让位、开关不压第一个表单控件、不戳出卡右缘"。
    """
    snaps = [s for _t, s in merged]
    info(f"S2/{label} 取样", f"{len(merged)} 帧；" + _bucket_line(snaps))
    rails = [s["rail_w"] for s in snaps]
    row_ws = [s["row"].width() for s in snaps]
    info(f"S2/{label} 列宽/开关宽",
         f"功能列 {min(rails)}~{max(rails)}px；开关 {min(row_ws)}~{max(row_ws)}px"
         f"（列满量程 {int(getattr(window, '_rx_rail_expanded', 0) or 0)}px）")

    # ---- S2-1：卡先让位并**钉住**（lead 2026-10-18 定的新口径）
    #   旧口径 q≥0.45 撤回：q 是**曲线后**的进度，STANDARD 前段极快，q=0.45 只花
    #   18% 时间；0.85 ≈ 时间上 37~49% 到位。新口径：
    #   ① 所有 q ≥ 0.90 的帧卡宽 == 满量程（既到位又钉住到头）；
    #   ② 首次满宽的 q* ≤ 0.90（信息项，实测打印）；
    #   ③ 钉住区间内不许有 >2px 的波动 —— "先 310 再 296"这类非单调直接 FAIL。
    full = int(getattr(window, "_rx_full", {}).get("config_w", 0) or 0)
    if full <= 0:
        full = max(s["card_w"] for s in snaps) or 1
    hold = [(1.0 - s["t"], s["card_w"]) for s in snaps
            if (1.0 - s["t"]) >= S2_HOLD_Q]
    if not hold:
        skip(f"S2-1/{label} 满宽钉住", f"没有 q≥{S2_HOLD_Q:.2f} 的帧")
    else:
        low = min(w for _q, w in hold)
        first_full = [1.0 - s["t"] for s in snaps if s["card_w"] >= full - 1]
        q_star = min(first_full) if first_full else float("nan")
        wiggles = [(q1, w1, q2, w2) for (q1, w1), (q2, w2)
                   in zip(hold, hold[1:]) if abs(w2 - w1) > 2]
        ok = low >= full - 1 and not wiggles and q_star <= S2_HOLD_Q
        detail = (f"q≥{S2_HOLD_Q:.2f} 的 {len(hold)} 帧最小卡宽 {low}/{full}px"
                  f"（{100.0 * low / max(1, full):.0f}%）；首次满宽 q*={q_star:.2f}"
                  f"（上限 {S2_HOLD_Q:.2f}）")
        if wiggles:
            q1, w1, q2, w2 = wiggles[0]
            detail += (f"；钉住区间波动 {len(wiggles)} 次，最狠 q={q1:.2f}:{w1}px → "
                       f"q={q2:.2f}:{w2}px")
        check(f"S2-1/{label} q≥{S2_HOLD_Q:.2f} 全程卡宽==满量程（让位并钉住）",
              ok, detail)

    # ---- S2-2：开关任何一帧都不许压到卡内第一个表单控件 cmb_link
    hit_link = []
    for s in snaps:
        link = s["link"]
        if link is None or not s["row_vis"]:
            continue
        inter = s["row"].intersected(link)
        if inter.width() > 0 and inter.height() > 0:
            hit_link.append((s, inter.width() * inter.height()))
    if hit_link:
        bad, area = max(hit_link, key=lambda p: p[1])
        detail = (f"压到 {len(hit_link)}/{len(snaps)} 帧，最大 {area}px² "
                  f"@p={bad['t']:.2f}；开关 y={bad['row'].y()}~"
                  f"{bad['row'].y() + bad['row'].height()}，cmb_link y="
                  f"{bad['link'].y()}~{bad['link'].y() + bad['link'].height()}")
    else:
        detail = f"0/{len(snaps)} 帧压到 cmb_link"
    check(f"S2-2/{label} 开关与 cmb_link 交集面积为 0（任何一帧）",
          not hit_link, detail)

    # ---- S2-3：已经进入卡的 x 范围的那些帧，开关右缘不许戳出卡右缘 +2px
    inside = [s for s in snaps
              if s["card"].x() <= s["row"].x() <= s["card"].x() + s["card"].width()]
    sticking = [s for s in inside
                if s["row"].x() + s["row"].width()
                > s["card"].x() + s["card"].width() + 2]
    if sticking:
        bad = max(sticking, key=lambda s: (s["row"].x() + s["row"].width())
                  - (s["card"].x() + s["card"].width()))
        over = ((bad["row"].x() + bad["row"].width())
                - (bad["card"].x() + bad["card"].width()))
        detail = (f"{len(sticking)}/{len(inside)} 帧戳出去，最多 +{over}px "
                  f"@p={bad['t']:.2f}（卡 {bad['card_w']}px，开关 "
                  f"{bad['row'].width()}px）")
    else:
        detail = f"卡 x 范围内的 {len(inside)} 帧都没戳出去"
    check(f"S2-3/{label} 开关右缘 ≤ 卡右缘 +2px", not sticking, detail)


def scenario_s2_reserve(app, window) -> None:
    """S2「提前预留开关位」：卡先长回满宽让位，开关再飞入落位。

    两个方向都量（退出 = 卡长回、展开 = 卡收走），因为"预留"是**时间上提前**的
    动作，只测一个方向看不出到底谁先动。
    """
    for label, start_expanded in (("退出", True), ("展开", False)):
        if start_expanded and not window._expanded:
            _run_transition(app, window._expand_receive)
            pump(app, 0.45)
        if not start_expanded and window._expanded:
            _run_transition(app, window._collapse_receive)
            pump(app, 0.45)
        action = window._collapse_receive if start_expanded else window._expand_receive
        with FrameTap(lambda: _switch_sample(window)) as tap:
            rows = tap.run(action, settle_ms=2500)
        merged = _merge_subframes(rows)
        if len(merged) < 4:
            skip(f"S2/{label} 取样", f"只采到 {len(merged)} 帧")
            continue
        _s2_analyse(window, label, merged)
        if label == "退出":
            # lead 指定：退出段 (t, q, card_w) **逐帧**序列，亲眼确认单调性，
            # 不用分桶摘要（分桶会掩盖"先 310 再 296"这类非单调回落）。
            print(f"  ---- S2/{label} 逐帧序列 ----")
            print("      t      q    card_w  开关x")
            for _t, s in merged:
                print(f"    {s['t']:.3f}  {1.0 - s['t']:.3f}   {s['card_w']:4d}   "
                      f"{s['row'].x()}")
        pump(app, 0.3)


# ------------------------------------------------- S3 历史数据「弹窗」动画

def _history_button(window):
    """找「打开历史数据」那颗按钮。

    调用链（2026-10-18 只读 grep 记下的，**没有改主程序**）：
      main_window.py:1148   self.btn_open_history = InkButton("打开历史数据", ...)
      main_window.py:1151   btn_open_history.clicked.connect(self._open_history)
      main_window.py:3756   def _open_history(self):
      main_window.py:3766       rows = rx_history.all_lines()      # 空 → toast 后返回
      main_window.py:3770       self._enter_replay(rows)           # 同步灌进接收区
      main_window.py:3800       self._replay_bar.show()            # 提示条，硬占位 50px
    ⚠️ 全工程 QDialog 只有 ``ui_settings.SettingsDialog`` 与 ``updater.UpdateDialog``，
       没有"历史数据弹窗" —— 所以 S3 量的是**回放模式这个开关**（有过渡 / 能关），
       不是弹窗。用户要的两件事（动画、再点能关）都能在现有接口上量。
    """
    btn = getattr(window, "btn_open_history", None)
    if btn is not None:
        return btn
    from PySide6.QtWidgets import QAbstractButton     # 接口改名后的兜底
    for w in window.findChildren(QAbstractButton):
        try:
            if "历史" in w.text():
                return w
        except RuntimeError:
            continue
    return None


def _poll(app, seconds: float, sample) -> list[tuple[float, dict]]:
    """按 ~2ms 轮询采样（回放开关的过渡不一定挂在 ticker 上，所以不走 FrameTap）。"""
    out: list[tuple[float, dict]] = []
    t0 = time.perf_counter()
    end = t0 + seconds
    while time.perf_counter() < end:
        app.processEvents()
        out.append(((time.perf_counter() - t0) * 1000.0, sample()))
        time.sleep(0.002)
    return out


def _changed_frames(series) -> tuple[int, float, float]:
    """返回 (值变化帧数, 首次变化时刻, 末次变化时刻)；``series`` 是 (t, 值)。

    回放提示条只有"高"这一个自由度，所以直接比高度序列，不用屏幕像素。
    """
    changes = 0
    first = last = 0.0
    for (t1, v1), (t2, v2) in zip(series, series[1:]):
        if v1 != v2:
            changes += 1
            if first == 0.0:
                first = t2
            last = t2
    return changes, first, last


def _poll_pixels(app, seconds: float, sample, grabber, trigger=None):
    """轮询采样：每帧抓一次屏（1:1 BitBlt）算与上一帧的变化点，同时取业务量。

    ``trigger`` 在**抓了第一帧之后**才调 —— 这样"点击那一瞬间"的变化也会被算进
    第一帧差值里（和 P2 同一手法）。
    """
    out: list[tuple[float, dict, int]] = []
    prev = grabber.grab()
    t0 = time.perf_counter()
    if trigger is not None:
        trigger()
    t_click = time.perf_counter()       # 时间戳相对**点击**（用户感知的起点）
    end = t0 + seconds
    while time.perf_counter() < end:
        app.processEvents()
        cur = grabber.grab()
        out.append(((time.perf_counter() - t_click) * 1000.0, sample(),
                    changed_points(prev, cur, grabber.width, grabber.height)))
        prev = cur
        time.sleep(0.002)
    return out


def _poll_state(app, seconds: float, sample, trigger=None):
    """**便宜通道**轮询：只取控件状态，不抓屏（processEvents + sleep 2ms）。

    为什么必须有它：BitBlt 通道（``_poll_pixels``）实测采样周期 40~46ms
    （日志区 2304×1284 的 1:1 GetDIBits 太贵），对一条 220ms 的坡最多看到
    5~6 个采样，``_changed_frames`` 的时长被压成 (变化数−1)×周期 —— S3①
    量到的"3 帧变化/116ms"就是这么来的采样伪象，不是动画真只有 3 个台阶
    （工程师 4ms widget 级复测：11 个高度、首变 33.5ms、末变 224ms、单调）。
    ① 的高度序列必须走这条通道才有分辨力；像素证据（②③）仍走 BitBlt 通道。

    时间戳相对**点击**（trigger 返回时刻）：``_settle_rx_layout`` 故意在起补间
    之前同步付掉一次大重排（否则那次重绘会把计时器堵 100ms），点击 → 补间
    真正起跑之间本来就有 40~60ms 空档 —— 用户感知的动画时长应从点击起算
    （lead 2026-10-19 终裁），不是"末变 − 首变"（那个量的是坡的陡峭段）。
    """
    out: list[tuple[float, dict]] = []
    t0 = time.perf_counter()
    if trigger is not None:
        trigger()
    t_click = time.perf_counter()
    end = t0 + seconds
    while time.perf_counter() < end:
        app.processEvents()
        out.append(((time.perf_counter() - t_click) * 1000.0, sample()))
        time.sleep(0.002)
    return out


def scenario_s3_history(app, window) -> None:
    """S3「回放开关」：点「打开历史数据」要有过渡，再点同一颗按钮要能关。

    不依赖任何"历史弹窗"类（全工程没有，调研已确认）。接口名固定为
    ``btn_open_history`` / ``_replay_active`` / ``_replay_bar``，所以工程师把
    "瞬切"换成动画之后，这段自动可跑、不用再改探针。

    ① 打开（**便宜通道**，2ms 状态轮询、不抓屏，时间轴以 **Tween.start 打戳**
       为 0）：``_replay_active`` False→True；**不同高度 ≥8**（EASE 理论 13）、
       **末变 ∈ [200,232]ms**（证明 220ms 跑满，与曲线无关）、
       **首个 ≥2px 可见变化 ≤40ms**（EASE 验收点）、高度单调不减、
       首个 active 帧非满高 —— BitBlt 通道 40~46ms/采会把坡压成 3 台阶/116ms，
       高度序列必须有 2ms 分辨力（见 ``_poll_state`` docstring）；
    ② 换文本有过渡（**像素通道**，重开一次取样）：接收区那块像素的变化量摊在
       ≥3 帧上、单帧占比 ≤60%（一帧换完 = 无过渡，如实判 FAIL 并注明）；
    ③ 再点同一颗按钮（**像素通道**）：``_replay_active`` 450ms 内变 False，且
       提示条**渐缩**（消失前 ≥2 帧变化、≥50ms）而不是首帧 hide。
    顺序：①开（便宜）→ ③关（像素）→ ②再开（像素）；②单独开一拍是因为
    ①的通道不抓屏、换文本的像素证据得有自己的采样。
    """
    btn = _history_button(window)
    if btn is None:
        skip("S3/找到「打开历史数据」入口",
             "没有 btn_open_history，也搜不到含「历史」的按钮")
        return
    try:
        info("S3/入口", f"{type(btn).__name__}「{btn.text()}」（可见 {btn.isVisible()}）")
    except RuntimeError:
        skip("S3/入口", "按钮已析构")
        return
    if getattr(window, "_replay_active", False):
        window._exit_replay()
        pump(app, 0.3)

    # 造 8 行样本：探针进程没连过串口，历史文件通常是空的 → _open_history 会直接
    # toast 返回，什么都测不到。**只在内存里**替换返回值，不写任何文件。
    try:
        import rx_history
    except ImportError:
        skip("S3/历史数据入口", "导入 rx_history 失败（模块不存在或改名）")
        return
    orig_all = getattr(rx_history, "all_lines", None)
    seeded = ["0000-00-00 00:00:00.000  1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16"] * 8
    if orig_all is not None:
        rx_history.all_lines = lambda *a, **k: list(seeded)

    # Tween.start 打戳：S3① 的时间轴以**补间真正起跑**为 0 —— `_enter_replay`
    # 的前缀（抓 rx shot + settle 大重排）有 ~40ms 空档，不扣掉会把前缀当动画
    # （lead 2026-10-19 终裁：150~450 span 口径作废）。打戳不改 main_window：
    # 包一层 Tween.start，认准 `_replay_bar_step*` 两个回调名，跑完还原。
    import ui_anim as _ua
    stamp: dict = {"t0": None}
    _orig_start = _ua.Tween.start

    def _stamped_start(self, start=0.0, end=1.0, duration=None):
        name = getattr(getattr(self, "_on_value", None), "__name__", "")
        if name in ("_replay_bar_step", "_replay_bar_step_down"):
            stamp["t0"] = time.perf_counter()
        return _orig_start(self, start, end, duration)

    _ua.Tween.start = _stamped_start

    # _Ticker._tick 打点（工程师 2026-10-19，5 行免费把"猜"变"知"）：四列通道
    # 自带采样干扰（processEvents 撞 pending paint 时单采 10~30ms，采样周期 =
    # 绘制成本），判不了 ticker 是否掉拍；tick 序列能。判读：间隔稳定 ~8ms
    # （120Hz 节拍）= 空洞在采样侧，结案；单次 ≥25ms = ticker 真掉一拍，
    # 记「已知偏差」不修（修法=补间期间给日志区预留 50px，用户已否）。
    tick_t: list[float] = []
    _t0_tick = {"v": 0.0}
    _orig_tick = _ua._Ticker._tick

    def _ticked(self):
        tick_t.append((time.perf_counter() - _t0_tick["v"]) * 1000.0)
        return _orig_tick(self)

    _ua._Ticker._tick = _ticked

    def sample() -> dict:
        try:
            bar = window._replay_bar
            bar_h = int(bar.height())
            bar_max = int(bar.maximumHeight())
            bar_hidden = bar.isHidden()
        except RuntimeError:
            bar_h, bar_max, bar_hidden = 0, 0, True
        t_tween = ((time.perf_counter() - stamp["t0"]) * 1000.0
                   if stamp["t0"] is not None else None)
        return {"active": bool(getattr(window, "_replay_active", False)),
                "bar_h": bar_h, "bar_max": bar_max, "bar_hidden": bar_hidden,
                "t_tween": t_tween,
                # _RxShot 快照 alpha（工程师 2026-10-19：S3② 补测时像素再黑，
                # alpha 序列也能证明渐变在走，别空手回来）
                "shot_alpha": getattr(getattr(window, "_rx_shot", None),
                                      "_alpha", None)}

    # 像素证据的取样区：提示条 + 接收区文本。提示条此刻是隐藏的，向上多留它
    # 的满高（50px）+ 余量，这样"提示条把自己挤出来"的那几十像素也在画面里。
    dpr = window.devicePixelRatioF()
    zone = QRect(_rect_of(window.txt_rx)).adjusted(0, -REPLAY_BAR_FULL - 24, 0, 0)
    zone = zone.intersected(window.frameGeometry())
    grabber = ScreenGrabber(zone, dpr)
    # 桌面活性门禁：黑屏时"变化点 0"是假象。① 的高度/激活序列是**控件读数**
    # 不受影响照判；②③ 的像素证据在黑屏时一律 SKIP（绝不 PASS）。
    alive0, _ad = _env_report("S3 前")
    try:
        # ── 三段计时采集（lead 2026-10-19 定稿口径，工程师采集要求）：
        #    click 打 t0，三段戳（click / Tween.start / 首次位移）拆开报，
        #    冷=本进程第一次进回放（抓图/换文本/重排的缓存全空），热=关掉再开。
        #    前缀的 _grab_rx_shot/setPlainText/_settle_rx_layout 开销跟**行数**
        #    直接相关 → 跑两组：8 行（冷1+热5）、500 行（冷1+热2），每组都记
        #    当次行数；报 p50/p95，不拿单次值定门槛。
        def _run_open(cold: bool = False) -> tuple[list[dict], dict]:
            stamp["t0"] = None
            if cold:                            # 冷跑重置 _tick 打点（锚=click）
                tick_t.clear()
                _t0_tick["v"] = time.perf_counter()
            t_click = time.perf_counter()
            btn.click()
            series: list[dict] = []
            end = time.perf_counter() + 0.6
            while time.perf_counter() < end:
                app.processEvents()
                s = sample()
                s["abs"] = time.perf_counter()
                series.append(s)
                time.sleep(0.002)
            first = next((s for s in series if s["bar_h"] != 0), None)
            return series, {"click": t_click, "tween": stamp["t0"],
                            "first_abs": first["abs"] if first else None,
                            "first_h": first["bar_h"] if first else None}

        def _three_seg(st: dict) -> tuple[float | None, float | None,
                                           float | None]:
            if st["tween"] is None or st["first_abs"] is None:
                return None, None, None
            return ((st["tween"] - st["click"]) * 1000.0,
                    (st["first_abs"] - st["tween"]) * 1000.0,
                    (st["first_abs"] - st["click"]) * 1000.0)

        def _ensure_closed() -> None:
            if getattr(window, "_replay_active", False):
                window._exit_replay()
            pump(app, 0.4)
            for _ in range(50):                 # 等上一轮的收尾补间归零
                if int(window._replay_bar.height()) == 0:
                    break
                pump(app, 0.02)

        def _seg_stats(runs) -> tuple | None:
            segs = [_three_seg(st) for _s, st in runs]
            ok = [t for t in segs if t[0] is not None]
            if not ok:
                return None
            def stat(v: list[float]) -> tuple[float, float]:
                v = sorted(v)
                mid = len(v) // 2
                p50 = v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2.0
                p95 = v[min(len(v) - 1, math.ceil(0.95 * len(v)) - 1)]
                return p50, p95
            cols = list(zip(*ok))
            return tuple(stat(list(c)) for c in cols), len(ok)

        runs_small: list[tuple[list[dict], dict]] = []
        for _i in range(6):                     # 8 行：冷1 + 热5
            _ensure_closed()
            runs_small.append(_run_open(cold=(_i == 0)))
        big_rows = [f"0000-00-00 00:{i // 60:02d}:{i % 60:02d}.{i % 1000:03d}"
                    "  1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20"
                    for i in range(500)]
        rx_history.all_lines = lambda *a, **k: list(big_rows)
        runs_big: list[tuple[list[dict], dict]] = []
        for _i in range(3):                     # 500 行：冷1 + 热2
            _ensure_closed()
            runs_big.append(_run_open())
        rx_history.all_lines = lambda *a, **k: list(seeded)   # 像素段用回 8 行

        # ── 读数①③：取 8 行**冷**跑（与 lead"实测 30ms"同口径；工程师
        #    反解定案：动画逐点命中 EASE，首变 30ms、末变 200ms、13 台阶）
        series0 = runs_small[0][0]
        became = next((True for s in series0 if s["active"]), None)
        check("S3/① 点击后 _replay_active 变 True", became is not None,
              "450ms 内仍为 False" if became is None else "已变 True")

        tweened = [(s["t_tween"], s) for s in series0
                   if s["t_tween"] is not None]
        first_active = next((s for s in series0 if s["active"]), None)
        check("S3/① 首拍非满高（maxH <50，地板 1px）",
              first_active is not None
              and 0 <= first_active["bar_max"] < REPLAY_BAR_FULL,
              f"首拍 maxH="
              f"{first_active['bar_max'] if first_active else '?'}px"
              f"（满 {REPLAY_BAR_FULL}px）")
        if not tweened:
            for _nm in ("读数① 首个 ≥2px（maxH）", "不同高度", "末变窗口",
                        "高度单调"):
                skip(f"S3/① {_nm}",
                     "没抓到 _replay_bar_step* 的 Tween.start（补间没起？）")
        else:
            # ⚠️ 把 bar_max ≥1000 的行（_replay_bar_settle 的还原动作，把上限
            # 放开到 16777215）从补间序列里剔掉 —— 工程师 2026-10-19 指正：
            # 它不是补间值，混进来会把"末变"虚报成 settle 时刻、台阶数多算 1。
            steps = [(tt, s["bar_max"]) for tt, s in tweened
                     if s["bar_max"] < 1000]
            settle_t = next((tt for tt, s in tweened
                             if s["bar_max"] >= 1000), None)
            changes, _f, last_t = _changed_frames(steps)
            distinct = len({h for _tt, h in steps if h > 0})
            first2 = next((tt for tt, h in steps if h >= 2), None)
            mono = all(v2 >= v1
                       for (_t1, v1), (_t2, v2) in zip(steps, steps[1:]))
            check("S3/① 不同高度 ≥ 8（EASE 理论 13 个台阶）",
                  distinct >= REPLAY_MIN_STEPS,
                  f"{distinct} 个不同高度（含首帧 1px，去重计数——采样相位"
                  f"会漏 1~2 个中间台阶，理论 13，判据看下限）")
            # 「末变」拆两条（工程师 2026-10-19）：最后高度变化（值走完 49→50，
            # 理论 192ms）与 settle（补间跑满 + 收尾还原，理论 220+一帧）是两个
            # 独立读数 —— 将来 _REPLAY_BAR_MS 改短时两个数一起动，合并写就看
            # 不出哪个坏了。lead 的 [200,232] 窗口落在 settle 上（它才是
            # "跑满 220ms"的直接证据，就是 on_finished）。
            info("S3/① 最后高度变化（49→50，理论 192ms）",
                 f"{last_t:.0f}ms" if changes else "整段无变化")
            check("S3/① settle（补间跑满+收尾还原）∈ [200,232]ms",
                  settle_t is not None
                  and REPLAY_LAST_CHANGE_MS[0] <= settle_t
                  <= REPLAY_LAST_CHANGE_MS[1],
                  f"settle {settle_t:.0f}ms（t 相对 Tween.start）"
                  if settle_t is not None else "0.6s 窗口内没等到 settle 还原")
            check(f"S3/① 读数① 首个 ≥2px（maxH）≤ "
                  f"{REPLAY_FIRST2PX_MAX_MS:.0f}ms（EASE 验收点）",
                  first2 is not None and first2 <= REPLAY_FIRST2PX_MAX_MS,
                  f"{first2:.0f}ms" if first2 is not None else "全程 <2px")
            check("S3/① 高度单调不减", mono,
                  "单调" if mono else "有回落（展开方向不该缩）")
            # 四列原始变化序列（t/maxH/H/hidden）：给工程师判读
            info("S3/① 高度变化序列（8 行冷跑，t 相对 Tween.start）",
                 f"{len(tweened)} 采样 / {changes} 次变化；变化点四列：")
            print("      t_ms  maxH    H  hidden")
            prev_key = None
            for tt, s in tweened:
                key = (s["bar_max"], s["bar_h"])
                if key != prev_key:
                    print(f"    {tt:6.0f}  {s['bar_max']:4d}  {s['bar_h']:3d}  "
                          f"{s['bar_hidden']}")
                    prev_key = key

        # ── 读数② 三段计时结果（门槛是 lead 初值，实测回来复核再定稿；
        #    报数不报结论；总 >200ms 按工程师要求标红）
        for label, runs, nrows in (("8 行", runs_small, 8),
                                   ("500 行", runs_big, 500)):
            stats = _seg_stats(runs)
            cold = _three_seg(runs[0][1])
            if stats is None or cold[0] is None:
                skip(f"S3/② 三段计时（{label}，{nrows} 行）",
                     "没抓到 Tween.start 或首位移（补间没起？）")
                continue
            (p50p, p95p), (p50c, p95c), (p50t, p95t) = stats[0]
            n = stats[1]
            info(f"S3/② 三段计时明细（{label}；冷=第 1 次）",
                 f"冷：前缀 {cold[0]:.0f} / 冷启动 {cold[1]:.0f} / 总 "
                 f"{cold[2]:.0f} ms；热 n={n - 1}："
                 f"前缀 p50 {p50p:.0f} / p95 {p95p:.0f}，"
                 f"冷启动 p50 {p50c:.0f} / p95 {p95c:.0f}，"
                 f"总 p50 {p50t:.0f} / p95 {p95t:.0f} ms")
        cold8 = _three_seg(runs_small[0][1])
        if cold8[0] is not None:
            red = "（⚠️ 超 200ms，工程师要求标红重议）" if cold8[2] > 200 else ""
            check("S3/② 三段·前缀 ≤60ms（8 行冷跑；初值门槛，lead 复核定稿）",
                  cold8[0] <= 60.0, f"前缀 {cold8[0]:.0f}ms")
            check("S3/② 三段·click→首位移 ≤150ms（8 行冷跑；初值门槛）",
                  cold8[2] <= 150.0,
                  f"总 {cold8[2]:.0f}ms{red}")
        # 工程师要求分开记的两条事实：动画干净（①③）与冷启动一次性成本
        if cold8[1] is not None:
            info("S3/① 冷启动一次性成本（独立记录，不参与 ①③ 判定）",
                 f"Tween.start→首拍落地 {cold8[1]:.0f}ms（归因：日志区被顶开 "
                 f"1px 的整块冷重绘 + _RxShot 首绘 + 条首绘；第二拍起 3~4ms）")
        # _tick 序列判读（工程师 2026-10-19）：掉拍 ≥25ms 记已知偏差不修
        if len(tick_t) >= 2:
            gaps = [b - a for a, b in zip(tick_t, tick_t[1:])]
            worst = max(gaps)
            info("S3/① _Ticker._tick 序列（8 行冷跑，ms 相对 click）",
                 f"前 30 拍={[round(v, 1) for v in tick_t[:30]]}；拍间隔 "
                 f"max={worst:.1f} / med={statistics.median(gaps):.1f}ms"
                 f"（共 {len(tick_t)} 拍）")
            if worst >= 25.0:
                info("S3/① _Ticker 停顿（记已知偏差，不修）",
                     f"冷跑出现单次 {worst:.1f}ms（≈{worst / 8:.0f} 拍，"
                     "ticker 8ms/拍）的事件循环停顿——一帧合并多拍进度，"
                     "值仍忠实于进度（Tween 按真实时间算 progress）")
            else:
                info("S3/① _Ticker 无停顿",
                     f"最大间隔 {worst:.1f}ms <25ms —— 四列序列里的空洞归"
                     "采样侧（processEvents 撞 pending paint），结案")

        # ③ 关：像素通道（渐缩证据）—— 先关掉，给 ② 腾出"再开一拍"
        closing = _poll_pixels(app, REPLAY_CLOSE_MS / 1000.0 + 0.08,
                               sample, grabber, trigger=btn.click)
        off_at = next((t for t, s, _n in closing if not s["active"]), None)
        check("S3/③ 二次点击 450ms 内 _replay_active 变 False",
              off_at is not None,
              "仍为 True（二次点击只是重新载入，没有 toggle）"
              if off_at is None else f"{off_at:.0f}ms 变 False")
        alive_s3 = alive0 and _screen_alive()[0]        # 拍完复核
        hide_at = next((t for t, s, _n in closing if s["bar_hidden"]), None)
        if not alive_s3:
            skip("S3/③ 提示条渐缩而不是首帧 hide", "桌面全黑，像素判据不可信")
        elif hide_at is None:
            check("S3/③ 提示条渐缩而不是首帧 hide", False, "450ms 内一直可见")
        else:
            before = [(t, s["bar_h"]) for t, s, _n in closing if t < hide_at]
            shrink, s_first, s_last = _changed_frames(before)
            check("S3/③ 提示条渐缩而不是首帧 hide",
                  shrink >= 2 and hide_at >= 50.0,
                  f"消失于 {hide_at:.0f}ms；之前 {len(before)} 帧里高度变化 "
                  f"{shrink} 帧（{s_first:.0f}→{s_last:.0f}ms）")

        # ② 再开：像素通道（换文本渐变证据）
        reopened = _poll_pixels(app, 0.5, sample, grabber, trigger=btn.click)
        alive_s2 = alive0 and _screen_alive()[0]        # 拍完复核
        # _RxShot alpha 序列（控件读数，工程师 2026-10-19 要求：像素再黑也不能
        # 空手回来——渐变在不在走，这列就是证据）
        aseq: list[tuple[int, float]] = []
        _last_a = None
        for _t, _s, _n in reopened:
            al = _s.get("shot_alpha")
            if al is None:
                continue
            if _last_a is None or abs(al - _last_a) > 0.02:
                aseq.append((round(_t), al))
                _last_a = al
        if aseq:
            info("S3/② _RxShot alpha 序列（控件读数，只列变化点）",
                 "；".join(f"{al:.3f}@{t}ms" for t, al in aseq[:12])
                 + ("…" if len(aseq) > 12 else ""))
        counts = [n for _t, _s, n in reopened]
        total = sum(counts)
        moving = [n for n in counts if n > PIXELS_MOVING]
        if not alive_s2:
            skip("S3/② 换文本有过渡（变化摊在多帧、单帧 ≤60%）",
                 "桌面全黑，像素判据不可信")
        elif total <= 0:
            check("S3/② 换文本有过渡（变化摊在多帧）", False,
                  "接收区一个像素都没变（样本没灌进去？）")
        else:
            share = max(counts) / total
            check("S3/② 换文本有过渡（变化摊在多帧、单帧 ≤60%）",
                  len(moving) >= 3 and share <= 0.6,
                  f"变化帧 {len(moving)} 个 / 最大单帧占比 {share * 100:.0f}%；"
                  f"总变化点 {total}；前 8 帧 {counts[:8]}")
    finally:
        _ua.Tween.start = _orig_start          # 打戳包装还原（先于一切清理）
        _ua._Ticker._tick = _orig_tick         # _tick 打点还原
        grabber.close()
        if orig_all is not None:
            rx_history.all_lines = orig_all
        try:
            if getattr(window, "_replay_active", False):
                window._exit_replay()
        except RuntimeError:
            pass


# --------------------------------------------------------- S 套汇总入口

def run_suite_s(app, window, which: str = "ALL") -> None:
    """依次跑 S1/S2/S3；``which`` 取 S1 / S2 / S3 / ALL。

    每段都包了 try：探针不许因为一处崩掉整轮（剩下两段的信息同样值钱）。
    """
    which = which.upper()
    plan = (("S1", scenario_s1_font),
            ("S2", scenario_s2_reserve),
            ("S3", scenario_s3_history))
    for name, fn in plan:
        if which not in ("ALL", "S", name):
            continue
        print(f"\n---- {name} " + "-" * 62)
        try:
            fn(app, window)
        except Exception as exc:                    # noqa: BLE001 —— 见 docstring
            check(f"{name}/执行未抛异常", False, f"{type(exc).__name__}: {exc}")
        pump(app, 0.4)


def main_suite_s(which: str = "ALL") -> int:
    """S 套独立入口（自带窗口、自带配置还原；不碰 ``main()`` 的路径）。"""
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))

    import hashlib
    from ui_anim import display_max_hz, frame_hz, set_frame_hz
    default_hz = frame_hz()
    hi_hz = display_max_hz()
    set_frame_hz(hi_hz)
    print(f"显示器上限 {hi_hz}Hz（探测节拍按它走）；默认 {default_hz}Hz")
    stamp = []
    for name in ("main_window.py", "ui_anim.py", "ui_surface.py", "ui_settings.py"):
        try:
            digest = hashlib.sha256(
                open(os.path.join(_ROOT, name), "rb").read()).hexdigest()[:12]
            stamp.append(f"{name}:{digest}")
        except OSError:
            stamp.append(f"{name}:?")
    print("源码指纹 " + "  ".join(stamp))
    print(f"套别 S（{which}）：S1 字体不干涉 / S2 提前预留开关位 / S3 回放开关")

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    from main_window import MainWindow
    window = MainWindow()
    window._port_watch.stop()
    window.setGeometry(0, 0, 1180, 860)
    window.show()
    # 与 main() 同样的理由：用户起手是 startup_maximized=true，不先摆到真实
    # 起手状态的话，第一段动画会混进 OS 级整窗几何变化。
    window.apply_startup_state()
    pump(app, 1.2)
    print(f"起手状态 fullscreen={window.isFullScreen()}  "
          f"窗口 {window.geometry().width()}x{window.geometry().height()}  "
          f"dpr={window.devicePixelRatioF()}")

    try:
        run_suite_s(app, window, which)
    finally:
        try:
            if window._expanded:
                window._collapse_receive()
                pump(app, 1.0)
        except Exception:                           # noqa: BLE001
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

    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    print("\n" + "=" * 96)
    print("S 套（字体不干涉 / 提前预留开关位 / 回放开关）结论表")
    print("=" * 96)
    print(f"{'判定':<6}{'项目':<52}{'数字'}")
    print("-" * 96)
    for name, ok, detail in _RESULTS:
        short = detail if len(detail) <= 38 else detail[:37] + "…"
        print(f"{'PASS' if ok else 'FAIL':<6}{name:<52}{short}")
    for name, detail in _SKIPPED:
        short = detail if len(detail) <= 38 else detail[:37] + "…"
        print(f"{'SKIP':<6}{name:<52}{short}")
    print("-" * 96)
    print(f"合计：{passed}/{total} PASS，另有 {len(_SKIPPED)} 条 SKIP（接口未定）")
    print("=" * 96)
    return 0 if (passed == total and not _SKIPPED) else 1


def scenario_p_settings(app, window, dpr: float) -> None:
    """P「换帧后静置」：换帧完成后再观测 300ms，相邻帧变化点 < 500。

    桌面活性门禁前置：黑屏时"变化点 0"是假象，SKIP 绝不 PASS；观测中每帧记
    luma，黑帧 INVALID 剔除后不足 3 帧也 SKIP（区分"中途变黑"与"帧太少"）。
    """
    alive, _ad = _env_report("P 前")
    if not alive:
        skip("P/设置换帧后相邻帧变化点 < 500",
             "桌面全黑，像素判据不可信（锁屏/显示器休眠/无合成）")
        close_settings(app, window)
        return
    if land_settings(app, window):
        dialog = window._settings_dialog
        rect = dialog.frameGeometry().adjusted(-48, -48, 48, 48)
        grabber = ScreenGrabber(rect, dpr)
        try:
            pump(app, 0.08)
            frames = measure_settings_landing(app, window, grabber)
        finally:
            grabber.close()
        frames = [f for f in frames if f[2] > 24]         # 黑帧 INVALID，剔除
        if len(frames) < 3:
            alive_now, _an = _screen_alive()
            skip("P/设置换帧后相邻帧变化点 < 500",
                 "有效帧太少：桌面中途变黑（黑帧已剔除）"
                 if not alive_now else f"帧太少（{len(frames)}）")
        else:
            counts = [c for _t, c, _l in frames]
            worst = max(counts)
            idx = counts.index(worst)
            check("P/设置换帧后相邻帧变化点 < 500", worst < PIXELS_CHANGED_MAX,
                  f"观测 {len(frames)} 有效帧 / {SETTINGS_LAND_MS:.0f}ms；"
                  f"最大 {worst} 点 @t={frames[idx][0]:.0f}ms；"
                  f"分布前5={sorted(counts, reverse=True)[:5]}")
    else:
        check("P/设置换帧后相邻帧变化点 < 500", False,
              "面板没有在 2.5s 内完成换帧（_settings_swap_target 未清）")
    close_settings(app, window)


# ------------------------------------------------------------------ main

def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))

    import ui_anim
    from ui_anim import display_max_hz, frame_hz, set_frame_hz
    default_hz = frame_hz()
    hi_hz = display_max_hz()
    set_frame_hz(hi_hz)
    print(f"显示器上限 {hi_hz}Hz（探测节拍按它走）；默认 {default_hz}Hz")
    # 给基线盖章：把被验收源码的指纹打出来（工程师可能正在改，基线必须可追）
    import hashlib
    stamp = []
    for _f in ("main_window.py", "ui_anim.py", "ui_surface.py", "ui_settings.py"):
        try:
            stamp.append(f"{_f}:{hashlib.sha256(open(os.path.join(_ROOT, _f), 'rb').read()).hexdigest()[:12]}")
        except OSError:
            stamp.append(f"{_f}:?")
    print("源码指纹 " + "  ".join(stamp))

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    from main_window import MainWindow
    window = MainWindow()
    window._port_watch.stop()
    window.setGeometry(0, 0, 1180, 860)
    window.show()
    # ⚠️ 必须用**用户真实起手状态**测量：配置里 startup_maximized=true，应用启动即整窗
    #    全屏。若在普通窗口上测，"展开接收区"会连窗口一起从 1180x860 跳到全屏 ——
    #    那是 OS 级瞬时几何变化，会把 80px 的单帧位移混进来，量的就不是卡片动画了。
    window.apply_startup_state()
    pump(app, 1.2)
    dpr = window.devicePixelRatioF()
    print(f"起手状态 fullscreen={window.isFullScreen()}  "
          f"窗口 {window.geometry().width()}x{window.geometry().height()}  dpr={dpr}")

    try:
        with GeometryTap(app, window) as tap:
            # ⓪ 门禁 + ① 设置：换帧后 300ms 的像素抖动（P2 场景自带门禁）
            scenario_p_settings(app, window, dpr)
            pump(app, 0.3)

            # ①-b 落定闪：从点击起连拍，专抓 P 跳过的那 100ms
            scenario_p2_settings_flash(app, window, dpr)
            pump(app, 0.3)

            # ② 接收区最大化 / ③ 退出
            scenario_expand(app, window, tap, "展开")
            pump(app, 0.5)
            scenario_collapse(app, window, tap, "退出")
            pump(app, 0.6)

            # ④ 中途打断（20% / 50% / 80% + 反方向）
            scenario_interrupts(app, window)
            pump(app, 0.4)

            # ⑤ 进度归因：是逻辑问题还是帧耗时
            scenario_progress_attribution(app, window)
            pump(app, 0.4)

            # ⑥ 帧率无关性：60Hz vs 165Hz
            if window._expanded:
                window._collapse_receive()
                pump(app, 1.2)
            set_frame_hz(60)
            pump(app, 0.2)
            d60 = duration_of(app, window, window._expand_receive)
            pump_until(app, lambda: not ui_anim.ticker_active(), 800)
            window._collapse_receive()
            pump(app, 1.2)
            set_frame_hz(hi_hz)
            pump(app, 0.2)
            dhi = duration_of(app, window, window._expand_receive)
            pump_until(app, lambda: not ui_anim.ticker_active(), 800)
            window._collapse_receive()
            pump(app, 1.0)
            delta = abs(dhi - d60) / max(1.0, d60)
            check(f"T/时长 60Hz({d60:.0f}ms) 与 {hi_hz}Hz({dhi:.0f}ms) 差 ≤15%",
                  delta <= DURATION_DELTA_MAX, f"相对差 {delta * 100:.1f}%")
            set_frame_hz(default_hz)

        # S 套（S1/S2/S3 用各自的 FrameTap / 轮询取样，不与 GeometryTap 嵌套）
        run_suite_s(app, window, "ALL")
    finally:
        close_settings(app, window)
        try:
            if window._expanded:
                window._collapse_receive()
        except Exception:                   # noqa: BLE001
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

    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)

    # ---- 可复制结论表 ----
    print("\n" + "=" * 96)
    print("三处动画 · 独立验收结论表")
    print("=" * 96)
    print(f"{'判定':<6}{'项目':<52}{'数字'}")
    print("-" * 96)
    for name, ok, detail in _RESULTS:
        short = detail if len(detail) <= 38 else detail[:37] + "…"
        print(f"{'PASS' if ok else 'FAIL':<6}{name:<52}{short}")
    print("-" * 96)
    print(f"合计：{passed}/{total} PASS"
          f"（G 速度比≤{SPEED_RATIO_MAX} / P 变化点<{PIXELS_CHANGED_MAX} / "
          f"I 反向幅度≤{INTERRUPT_AMP_MAX}× / T 时长差≤{DURATION_DELTA_MAX:.0%}）")
    print("=" * 96)
    return 0 if passed == total else 1


# ------------------------------------------------------- 单项跑（复测用）

def _boot_single():
    """单项跑的起手（与 main() 同一套起手：真实起手状态 + 同一节拍）。"""
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))

    from ui_anim import frame_hz, set_frame_hz, display_max_hz
    default_hz = frame_hz()
    set_frame_hz(display_max_hz())

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    from main_window import MainWindow
    window = MainWindow()
    window._port_watch.stop()
    window.setGeometry(0, 0, 1180, 860)
    window.show()
    window.apply_startup_state()
    pump(app, 1.2)
    return app, window, (default_hz, settings, snapshot)


def _teardown_single(app, window, ctx) -> None:
    default_hz, settings, snapshot = ctx
    close_settings(app, window)
    try:
        if window._expanded:
            window._collapse_receive()
            pump(app, 1.0)
    except Exception:                           # noqa: BLE001
        pass
    from ui_anim import set_frame_hz
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


def _print_results(title: str) -> int:
    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    print("\n" + "=" * 96)
    print(title)
    print("=" * 96)
    for name, ok, detail in _RESULTS:
        short = detail if len(detail) <= 58 else detail[:57] + "…"
        print(f"{'PASS' if ok else 'FAIL':<6}{name:<52}{short}")
    print("-" * 96)
    print(f"合计：{passed}/{total} PASS")
    print("=" * 96)
    return 0 if passed == total else 1


def main_p2() -> int:
    """P2 单项：事件驱动拍屏（不跑全套，其余判据已 PASS 不动）。"""
    app, window, ctx = _boot_single()
    dpr = window.devicePixelRatioF()
    try:
        scenario_p2_settings_flash(app, window, dpr)
    finally:
        _teardown_single(app, window, ctx)
    return _print_results("P2 单项（事件驱动拍屏）结论")


def main_pix() -> int:
    """像素项专用入口：P + P2（lead 2026-10-19：桌面 ALIVE 窗口短，
    FINAL 里三个像素场景各烧 3×20s 门禁重试；单项模式让你一确认桌面活
    就开跑，60~90s 内拿到 P/P2 两个数，不用等全套）。"""
    app, window, ctx = _boot_single()
    dpr = window.devicePixelRatioF()
    try:
        scenario_p_settings(app, window, dpr)
        pump(app, 0.3)
        scenario_p2_settings_flash(app, window, dpr)
    finally:
        _teardown_single(app, window, ctx)
    return _print_results("像素项：P + P2（桌面活性门禁照常；BLACK 一律 SKIP）")


def main_g_exit(runs: int = 3) -> int:
    """G′/退出 单项 × N 连跑（lead 2026-10-19：三段数值分别报，门槛 1.4）。"""
    app, window, ctx = _boot_single()
    try:
        with GeometryTap(app, window) as tap:
            for i in range(runs):
                scenario_collapse(app, window, tap, f"退出#{i + 1}")
                pump(app, 0.8)
    finally:
        _teardown_single(app, window, ctx)
    return _print_results(
        f"G′/退出 单项 ×{runs} 连跑结论（跟踪斜率 max≤{TRACK_SLOPE_MAX_TOL:g}×行程，"
        f"均值≤{TRACK_SLOPE_MEAN_TOL:g}×行程）")


def main_final() -> int:
    """终版：P + P2 + S3 + G′/退出×3（其余判据沿用上一冻结点数据，不重跑）。"""
    app, window, ctx = _boot_single()
    dpr = window.devicePixelRatioF()
    try:
        scenario_p_settings(app, window, dpr)
        pump(app, 0.3)
        scenario_p2_settings_flash(app, window, dpr)
        pump(app, 0.5)
        with GeometryTap(app, window) as tap:
            for i in (1, 2, 3):
                scenario_collapse(app, window, tap, f"退出#{i}")
                pump(app, 0.8)
        run_suite_s(app, window, "S3")
    finally:
        _teardown_single(app, window, ctx)
    return _print_results("终版验收：P + P2 + S3 + G′/退出×3（含桌面活性门禁）")


if __name__ == "__main__":
    # 位置参数可选：S/S1/S2/S3 → S 套；P2 → P2 单项（事件驱动）；
    # PIX → 像素项 P+P2（桌面一活就开跑，不用等全套）；
    # GEXIT → G′/退出 单项 ×3 连跑；FINAL → 终版（P+P2+S3+G′退出×3）；
    # 不带参数 → 原有全套。
    _WANT = [arg.upper() for arg in sys.argv[1:] if not arg.startswith("-")]
    if _WANT and _WANT[0] in ("S", "S1", "S2", "S3"):
        raise SystemExit(main_suite_s(_WANT[0]))
    if _WANT and _WANT[0] == "P2":
        raise SystemExit(main_p2())
    if _WANT and _WANT[0] == "PIX":
        raise SystemExit(main_pix())
    if _WANT and _WANT[0] in ("GEXIT", "G-EXIT"):
        raise SystemExit(main_g_exit())
    if _WANT and _WANT[0] == "FINAL":
        raise SystemExit(main_final())
    raise SystemExit(main())
