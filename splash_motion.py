"""开屏动画（planA / planB）：**纯原生几何绘制**，不播视频、不播帧序列、不查表。

设计稿是 ``assets/splash/*_4k60.mp4``，但播放端一行视频解码都没有 ——
素材只有两个矢量 SVG（``mark_mono_white.svg`` 图标 / ``lockup_on_dark|light.svg``
图标+文字），按目标尺寸实时渲染，所以是**真矢量、任意 2K/4K 都清晰**。

⛔ od46 起 **planA 与 planB 都是原生几何动画**（两套 ``MotionSpec`` 当前完全
同源，取值一致，只是预留了差异化接口）。设计稿 mp4 / poster 仍留在 assets
目录里不动 —— ``presets()/plans()/choice()`` 靠磁盘扫描，删/改名会让用户
QSettings 里的 choice 静默漂移（见 splash_anim）。

时间轴（几何段 3.20s = t_total；**淡出也正式计入** 0.30s，全程 3.50s。od47i 按用户
「落点+0.1s、图标左移+0.1s、淡出也算在内且用高品质高斯」在 od47h 基础上再调）
----------------------------------------
    0.00~0.30  空白（界面底色）
    0.30~1.00  图标 logo 高斯模糊淡入：sigma 9→0，alpha 0→1（0.70s）；
               淡入全程图标放大 10% 随进度收回 1.0×（呼吸式 settle，无尺寸跳变）
    1.00~1.70  图标左移 + 缩小（0.33→0.21 屏高，0.70s，+0.1s）
    1.70~2.30  换成「图标+文字」组合，文字从左往右逐渐显露（0.60s）
    1.70~1.90    └ 同时 SVG 原图 divider 从中央向上下展开（0.20s）
    2.30~2.40  停留（0.10s）
    2.40~3.20  整体缩到主窗页头 logo，落地即结束（0.80s，+0.1s）
    3.20~3.50  背景**高品质高斯**模糊淡出（0.30s，在 splash_anim，_FADE_MS=300）；
               淡出完成才算开屏结束

    ⚠️ od47i 段时长变更（相对 od47h，均为**段时长**增减、下游段顺延）：
       图标左移 0.60→0.70s（t_mark_end 1.60→1.70，t_text_end 2.20→2.30）；
       落点 0.70→0.80s（t_hold_end 2.30→2.40，t_land_end / t_total 3.00→3.20）；
       几何段 +0.2s → 3.20s；淡出正式计入 0.30s / 全程 3.50s。
       淡出 = 完整源的 _blur(1.0)（与淡入同源高品质高斯，见 _preheat / _paint）。

⛔ 所有段一律 ease 曲线（非线性），**没有任何查表数据** —— 上一版从设计稿逐帧
量包围盒，那些 ±1px 噪声插值后就是可见抖动。参数化曲线天生连续。

⛔ 终点**不写死像素**：运行时取 ``BrandHeader.ink_rect()`` 注入（``set_land``），
换屏 / 换 DPI / 分屏 都能精确落在软件界面自己的 logo 上。
墨迹内边距常量与 ``ui_brand.INK_FRAC`` 是**同一真值源**（见下方 import）。

⛔ 帧率由调用方（``splash_anim``）按设置里的「动画帧率」驱动，本模块只按
``t`` 出画面，不自己挂定时器。

分辨率自适应（od46）
--------------------
- 清晰档位图宽度 ``_clear_w`` 按**屏幕逻辑高**动态算：保证任意 t 的 ``bmp_w``
  ≤ ``bmp_w_max``，绘制时只会**缩小**不会放大（od45 固定 1600 在 1080p 放大
  1.49×、4K 放大 2.97×，清晰段糊）。
- 模糊半径用**屏幕逻辑空间常量** ``SIGMA_SCREEN=18.0``：不同机型屏幕上模糊
  半径恒定 18 逻辑 px。高斯在**缩小的固定工作分辨率** ``_blur_w``（clamp
  [450,1200]，与 dpr 无关）上算，算完**升回全分辨率**再缓存 —— 播放期 1:1
  画出 = 真模糊、零放大（od46 修正：旧实现直接拿低位图放大，看着像降分辨率）。
- 13 个 sigma 档 + 清晰档 + 淡出模糊图全部在 ``_preheat()`` 预计算；播放期
  ``paint()`` 只剩查表 + drawPixmap + fillRect，**零重渲染**。
- ⛔ 内存（od46 修正）：全分辨率模糊档 1080p@200% 约 20MB/张、13 档 ≈244MB，
  而播放器 ``_close()`` 只 close 不清 ``_motion``，会**常驻整个会话**；8K 级
  物理分辨率单张 ~85MB、13 档 1.1GB 直接 OOM。故：①按 ``_BLUR_CACHE_BUDGET`` 自适应
  减档（最少 3 档）；②淡入一结束就由 ``_motion_tick`` 调
  ``release_blur_cache()`` 清空档表（``_fade_pix`` 已单独持有引用不受影响），
  常驻降到「清晰档 + 淡出图」~40MB。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (QColor, QImage, QLinearGradient, QPainter, QPixmap,
                           QRadialGradient)

# 墨迹内边距的**单一真值源**：ui_brand 里 BrandHeader.ink_rect() 用的同一组分数。
# splash_motion 不另立一套（od45 曾复制成 0.0270/0.1745/0.948/0.638，与 ui_brand
# 的 31/1120、46/263、1060/1120、168/263 有 ≤0.16% 的取整差异，落点会微偏）。
# ui_brand 顶层只 import ui_controls / ui_theme，不反向 import splash_motion，
# 所以这里顶层引用不会循环 import。
from ui_brand import INK_FRAC as _BRAND_INK_FRAC

# ---- 素材几何 -------------------------------------------------------------
# 墨迹（含图标+文字）宽高比：必须与软件页头 lockup 完全一致，否则落点末帧 logo 宽度
# 与软件 logo 差若干像素。由 bitmap_aspect × ink_w_frac / ink_h_frac 精确导出
# （4.2595 × (1060/1120) / (168/263) ≈ 6.3114），取代原先「实测 948×150≈6.32」
# 的近似 —— 这一档 ~0.14% 宽度差就是落点宽度偏差（dw≈0.88px）的根。
_ASP_NUM = 6.3114
_DIV_X = 702.5 / 1576.0
_DIV_Y = 120.0 / 370.0
_DIV_W = 3.0 / 1576.0
_DIV_H = 128.0 / 370.0
# 分隔线左缘抗锯齿余量（SVG 宽度比例）。栅格化时 divider 左缘的 AA 会落到
# x < divider_x 的最后一列（实测整列 alpha≈33/255、几乎全高），若剪切点正好
# 取在 divider_x，这列残影会进入「图标-only」模糊源，被高斯晕成一条**提前
# 出现**的竖直淡带（用户 2026-11-04 / 11-06 两次投诉，tol=24 的探针会漏判）。
# 故剪切点与图标 clip 一律退让本余量。0.003×1576≈4.7 单位，远小于图标右缘到
# divider 的空隙（≈0.025 比例），不会裁到图标。
_DIV_CUT_GUARD = 0.003
_BITMAP_ASPECT = 4.2595                 # lockup viewBox 1576×370
# 模糊档缓存总预算（字节）。超过就自动减档，保证极端分辨率/高 DPI 不 OOM。
_BLUR_CACHE_BUDGET = 300 * 1024 * 1024


@dataclass(frozen=True)
class MotionSpec:
    """一套开屏几何动画的全部参数（frozen，播放期只读）。

    od46：planA / planB 各持有一份。planB 含「向左上角落点」段（``land=True``）；
    planA 与 planB 同源（素材/画质/曲线一致），唯一差异是 ``land=False`` ——
    在「左移缩小」后停在最终位置、不再向页头角落收（用户 2026-11-06：相比
    planB 仅少了「图标向左上角缩小」一段）。
    """

    # 时间轴（秒）
    t_blank_end: float = 0.30
    # ⛔ 各段时长按其自身意图**独立调整**、下游段随之顺延，而不是只推单个端点 ——
    #    只推一个端点会把相邻段时长压短/拉长，反而破坏流畅。
    # od47h：淡入段 +0.1s（0.60→0.70）、图标左移段 +0.1s（0.50→0.60）、落点 −0.1s。
    # od47i（用户 2026-11-05「落点加0.1s、图标左移加0.1s，淡出也算在内」）：
    #   以 od47h 为基准再各 +0.1s ——
    #     高斯模糊淡入  0.70s（0.30→1.00）不变；
    #     图标左移      0.60→0.70s（t_mark_end 1.60→1.70）；
    #     文字显露      0.60s（随 mark 顺延，2.20→2.30 完成）；
    #     停留          0.10s（2.30→2.40）；
    #     落点          0.70→0.80s（t_land_end 3.00→3.20）；
    #   几何段合计 3.20s；**淡出也正式计入时间轴**：3.20→3.50s（0.30s，全程 3.50s）。
    #   淡出用与淡入同源的**高品质高斯**（_fade_pix = 完整源的 _blur(1.0)，
    #   见 _preheat / _paint）。
    t_fade_end: float = 1.00            # 高斯模糊淡入时长 0.70s（0.30→1.00，不变）
    t_mark_end: float = 1.70            # od47i：图标左移段再 +0.1s（0.60→0.70s）
    t_text_end: float = 2.30            # 文字显露完成（随 mark 顺延 +0.1s）
    t_line_ms: float = 0.20             # 竖线展开时长（秒）
    t_hold_end: float = 2.40            # 停留起点（随 text 顺延 +0.1s）
    t_land_end: float = 3.20            # od47i：落点段再 +0.1s（0.70→0.80s）
    t_total: float = 3.20               # 几何段合计 3.20s（淡出另计 0.30s，全程 3.50s）

    # 几何（归一化，相对屏幕宽/高）
    h0: float = 0.330                   # 初始图标高
    h1: float = 0.210                   # 左移缩小后
    x1: float = 0.098                   # 左移后的墨迹左边缘
    sigma0: float = 9.0                 # 模糊档位的上限（步进用）

    # SVG 原图里的 divider（不要自己画）。全部相对**位图**归一化。
    divider_x: float = _DIV_X
    divider_y: float = _DIV_Y
    divider_w: float = _DIV_W
    divider_h: float = _DIV_H
    # 文字从竖线右边缘起 = divider_x + divider_w（写 0.405 会压到图标上）
    text_clip0: float = _DIV_X + _DIV_W

    # 墨迹内边距（单一真值源 ui_brand.INK_FRAC）
    ink_left: float = _BRAND_INK_FRAC[0]
    ink_top: float = _BRAND_INK_FRAC[1]
    ink_w_frac: float = _BRAND_INK_FRAC[2]
    ink_h_frac: float = _BRAND_INK_FRAC[3]

    # 素材几何
    lockup_aspect: float = _ASP_NUM
    bitmap_aspect: float = _BITMAP_ASPECT

    # 模糊：屏幕逻辑空间常量（不同机型恒为 18 逻辑 px 半径）
    sigma_screen: float = 18.0

    # 落点（图标准左上角收缩）：仅 planB 启用。planA 在「左移缩小」后停在最终
    # 位置，不再向页头角落收。两套方案素材/画质/曲线完全一致，唯一差异在此。
    land: bool = True

    # 收尾（淡出）的方式 —— 2026-11-07 用户点名两套方案要有区别：
    #   "all"（planA）：**整体高斯模糊淡出** —— lockup 从清晰逐档糊到最大档，
    #                  幕布晚一点再褪，让"糊开"这个过程看得见。
    #   "bg" （planB）：**背景高斯模糊淡出** —— lockup 全程保持清晰，只让幕布
    #                  糊散褪去（幕布是纯色，肉眼看到的是"清晰地浮在化开的底上"
    #                  然后一起消失），不做 lockup 模糊。
    fade_style: str = "all"

    # 淡出时长（毫秒）：planA 用户 2026-11-07「给 planA 的淡出加 0.1s」—— 默认 300，
    # SPEC_PLANA 提到 400；planB 仍走默认 300。淡出由 splash_anim._fade_step 按此值计时
    # （不再共用全局 _FADE_MS，否则改一处会同时动两套方案）。
    fade_ms: float = 300


# 两套方案：planB 含「向左上角落点」；planA 与 planB **同源**（素材/画质/曲线一致），
# 唯一差异是 planA 在「左移缩小」后停在最终位置、不再向页头角落收（见 MotionSpec.land）。
# planA 用户 2026-11-06 定调：空白 0.3s → 高斯淡入 → 左移缩小 → 文字右展 → 停留 0.2s
# → 高斯模糊淡出；相比 planB 仅少了「图标向左上角缩小」一段。
SPEC_PLANB = MotionSpec(land=True, fade_style="bg")   # planB：落点 + 背景模糊淡出
SPEC_PLANA = MotionSpec(                        # planA：去落点 + 停留 0.2s
    land=False,
    fade_style="all",       # 整体高斯模糊淡出（lockup 逐档糊开，幕布后褪）
    t_hold_end=2.50,        # 文字完成 2.30 → 停留 0.2s
    t_land_end=2.50,        # 无落点段，占位等值（_state 落点分支已被 land 关掉）
    t_total=2.50,           # 几何止于停留；随后等主窗就绪再高斯淡出
    fade_ms=400,            # 淡出 +0.1s（300→400ms），仅 planA；planB 仍 300
)

# ---- 兼容别名（od45 的模块级常量 → 单一真值源 SPEC_PLANB）------------------
# 历史 devtools 探针（如 devtools/_check_land.py）读 sm._INK_LEFT / sm.T_TOTAL，
# 保留这些名字指向 SPEC_PLANB，避免 import 期炸掉；**方法内部一律读 self._spec**。
LOCKUP_ASPECT = SPEC_PLANB.lockup_aspect
_INK_LEFT = SPEC_PLANB.ink_left
_INK_TOP = SPEC_PLANB.ink_top
_INK_W_FRAC = SPEC_PLANB.ink_w_frac
_INK_H_FRAC = SPEC_PLANB.ink_h_frac
_DIVIDER_X = SPEC_PLANB.divider_x
_DIVIDER_Y = SPEC_PLANB.divider_y
_DIVIDER_W = SPEC_PLANB.divider_w
_DIVIDER_H = SPEC_PLANB.divider_h
_TEXT_CLIP0 = SPEC_PLANB.text_clip0
_H0 = SPEC_PLANB.h0
_H1 = SPEC_PLANB.h1
_X1 = SPEC_PLANB.x1
_SIGMA0 = SPEC_PLANB.sigma0
T_BLANK_END = SPEC_PLANB.t_blank_end
T_FADE_END = SPEC_PLANB.t_fade_end
T_MARK_END = SPEC_PLANB.t_mark_end
T_TEXT_END = SPEC_PLANB.t_text_end
T_LINE_MS = SPEC_PLANB.t_line_ms
T_HOLD_END = SPEC_PLANB.t_hold_end
T_LAND_END = SPEC_PLANB.t_land_end
T_TOTAL = SPEC_PLANB.t_total
SIGMA_SCREEN = SPEC_PLANB.sigma_screen
# od45 的固定档位宽：播放期已不用（改读 self._clear_w / self._blur_w），
# 只作为「__init__ 未给屏幕尺寸时」的回退与历史兼容。
_BLUR_PX = 450
_CLEAR_PX = 1600


def _ease_in_out_cubic(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 4.0 * x * x * x if x < 0.5 else 1.0 - (-2.0 * x + 2.0) ** 3 / 2.0


def _ease_out_cubic(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1.0 - (1.0 - x) ** 3


def _smoothstep(x: float) -> float:
    """对称 smoothstep ``3x² − 2x³``：淡出包络用（两段错开的交叉溶）。

    ⛔ 落点段**不用**它 —— 对称曲线中段是宽平台（14.8/14.8）、首尾镜像，
    读起来偏匀速；且 ``f''(1) = −6`` 落定瞬间加速度突变，会有轻微「顿」。
    """
    x = min(1.0, max(0.0, x))
    return min(1.0, max(0.0, x * x * (3.0 - 2.0 * x)))


def _ease_land(x: float) -> float:
    """落点段：**先慢 → 后快 → 再慢**（长尾吸附）的临界阻尼型位移曲线。

    apple-design「Designing Fluid Interfaces」：位移类动作用 **damping 1.0**
    （临界阻尼、**无过冲**）—— 快速冲出后以长尾减速平滑落定，而不是撞停。
    这里用解析式精确实现，避免开 ODE / 引入弹簧状态（开屏是一次性脚本动画，
    不接手势，无需 interruptible，但**落点必须逐像素精确**，故严禁过冲）。

    ``f(x) = 6x² − 8x³ + 3x⁴``（Beta(2,3) 分布的 CDF），约束：
      · ``f(0)=0, f(1)=1``            —— 精确落到页头 logo，绝不欠/过冲
      · ``f'(x) = 12x(1−x)² ≥ 0``    —— 全程单调、无回弹
      · ``f'(0)=0``                   —— 从静止起步（慢）
      · 峰值 ``f'(1/3)=16/9≈1.78``    —— **x=1/3 就冲到最快**（后快）
      · ``f'(1)=0`` 且 ``f''(1)=0``   —— C² 平滑落定（再慢），速度/加速度
        都归零，与后接的停留/淡出严丝合缝，无「撞到终点」的顿挫

    对比旧对称 smoothstep（``3x²−2x³``，峰值 1.5 @ x=0.5、``f''(1)=−6``）：
    新曲线峰值**更靠前更饱满**、减速段**更长更绵**，每 0.1 位移
    ``5.2 / 12.9 / 16.8 / 17.7 / 16.3 / 13.3 / 9.6 / 5.7 / 2.4 / 0.37 %``，
    logo 像被页头「吸」进去后轻轻贴住，而非匀速平移到点再停 —— 这是
    「高级感」的来源。⛔ 别回退到 ``ease_in_out_cubic``（两端 dwell、中段线性）
    或 od47f 的 ``1.8x−0.6x²−0.2x³``（先快后慢，用户点名否过）。
    纯解析、可预计算、播放期零成本。
    """
    x = min(1.0, max(0.0, x))
    v = x * x * (6.0 - 8.0 * x + 3.0 * x * x)
    return min(1.0, max(0.0, v))   # 浮点尾差也别让插值越界（落点必须精确）


def _ease_in_cubic(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * x


def _seg(t: float, a: float, b: float) -> float:
    """把 t 映射到 [a,b] 上的 0~1。"""
    if b <= a:
        return 1.0 if t >= b else 0.0
    return min(1.0, max(0.0, (t - a) / (b - a)))


_GAUSSIAN_ERROR: str | None = None      # 供探针断言（⛔ 不许再静默降级）


def _gaussian(pix, sigma):
    """真高斯模糊，``pix`` 可为 QPixmap 或 uint8 (h,w,4) ndarray（返回同类型）。

    ndarray 形态供 ``_preheat`` 线程池并行调用：numpy 卷积释放 GIL，
    QPixmap/QImage 仍只在主线程构造（Qt 非线程安全）。
    """
    """真高斯模糊（numpy 可分离卷积，**不依赖 cv2/scipy**）。

    ⛔ 之前用 cv2 只为这一句话，代价是 41.5MB（cv2.pyd 29.1 + ffmpeg 12.4），
    是 onefile 冷启动慢的主因；用户 2026-11-04「可以砍掉，planA 以后也重写」。
    scipy 也不能引（~30MB，会把砍 cv2 省下的体积吐回去）。
    可分离卷积：水平一遍、垂直一遍，用滑动窗口视图 + einsum 做**向量化**
    （无 Python 级 kernel 循环）。原 Python 循环版在 13 档 × 793×150 图上
    实测 ~819ms，向量化后 ~60ms，且仍在 __init__ 同步预热能接受。

    ⛔⛔ od47 真 bug 修复（用户 2026-11-05「高斯模糊效果要准确」）：
    ``sliding_window_view`` 把**窗口轴追加到末尾**，实际 shape 是
    ``(h, w, 4, k)`` 而不是 ``(h, w, k, 4)``。原代码写
    ``np.einsum("hwkc,k->hwc", ...)`` → 维度不匹配抛 ValueError → 被
    ``except`` 静默吞掉 → **每次都返回原图**。结果 od46 的「向量化高斯」
    从未生效，淡入全程是一张 793px 图放大 6× 的糊图（用户一眼看穿
    「高斯模糊不是降低分辨率」）。正确写法是 ``"hwck,k->hwc"``。
    ⛔ 降级必须留痕：``_GAUSSIAN_ERROR`` + app_log，探针要断言它为 None。
    """
    global _GAUSSIAN_ERROR
    if sigma <= 0.05 or pix is None:
        return pix
    if isinstance(pix, QPixmap) and pix.isNull():
        return pix
    try:
        import numpy as np
        from numpy.lib.stride_tricks import sliding_window_view
        from PySide6.QtGui import QImage

        as_array = isinstance(pix, np.ndarray)
        if as_array:
            arr = pix.astype(np.float32)
            h, w = arr.shape[:2]
        else:
            img = pix.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            w, h = img.width(), img.height()
            raw = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(
                h, img.bytesPerLine())[:, : w * 4].reshape(h, w, 4)
            arr = raw.astype(np.float32)
        r = max(1, int(round(sigma * 2.0)))
        xs = np.arange(-r, r + 1, dtype=np.float32)
        kern = np.exp(-(xs ** 2) / (2.0 * sigma * sigma)).astype(np.float32)
        kern /= kern.sum()
        k = kern.shape[0]
        # 水平：pad 边缘 → sliding_window_view 把窗口轴放**最后** → (h,w,4,k)
        # ⚠️ 逐通道 np.dot 而不是 np.einsum("hwck,k->hwc")：水平滑动窗口在
        #    axis=1 上 stride 不连续，einsum 实测 16.8ms，dot 只要 7.7ms
        #    （结果 np.allclose 一致）；垂直连续，dot 同样不吃亏。
        hp = np.pad(arr, ((0, 0), (r, r), (0, 0)), mode="edge")
        sw = sliding_window_view(hp, k, axis=1)
        horiz = np.empty((h, w, 4), dtype=np.float32)
        for c in range(4):
            horiz[:, :, c] = np.dot(sw[:, :, c, :], kern)
        # 垂直：同理 (h,w,4,k) → (h,w,4)
        vp = np.pad(horiz, ((r, r), (0, 0), (0, 0)), mode="edge")
        sv = sliding_window_view(vp, k, axis=0)
        out = np.empty((h, w, 4), dtype=np.float32)
        for c in range(4):
            out[:, :, c] = np.dot(sv[:, :, c, :], kern)
        res = np.clip(out, 0.0, 255.0).astype(np.uint8)
        if as_array:
            return res
        qimg = QImage(np.ascontiguousarray(res).data, w, h, 4 * w,
                      QImage.Format.Format_RGBA8888).copy()
        return QPixmap.fromImage(qimg)
    except Exception as exc:              # noqa: BLE001 - 开屏不能挡启动，但必须留痕
        _GAUSSIAN_ERROR = repr(exc)
        try:
            import app_log
            app_log.warning(f"开屏高斯模糊降级：{exc!r}")
        except Exception:                 # noqa: BLE001
            pass
        return pix


class MotionRenderer:
    """按时间 t（秒）出画面。调用方每帧 ``paint(painter, w, h, t)``。"""

    def __init__(self, scheme: str, dpr: float,
                 screen_w: float = 1920.0, screen_h: float = 1080.0,
                 spec: MotionSpec = SPEC_PLANB) -> None:
        """screen_w / screen_h 是开屏窗口的**逻辑 px**（setGeometry 后有效）。

        ``_start_motion()`` 传 ``self.width(), self.height()``；缺省值只保护
        历史 devtools 探针（它们用 2 参数构造，不应炸）。
        """
        # screen_w 只为保持 5 参数签名兼容（大量 devtools/_check 探针按位置传
        # W, H），实际绘制宽高由 paint(painter, w, h, t) 传入，这里不用它。
        del screen_w
        self._scheme = scheme
        self._dpr = max(1.0, float(dpr))
        self._spec = spec
        # screen_h 仅用于按屏幕逻辑高动态算清晰/模糊档位；实际绘制宽高由
        # paint(painter, w, h, t) 的 w/h 传入，无需存 screen_w。
        self._screen_h = max(1.0, float(screen_h))
        self._cache: dict[tuple, QPixmap] = {}
        self._blur_cache: dict[float, QPixmap] = {}
        self._bg = QColor("#ffffff" if scheme == "light" else "#0d0d0f")
        # ⛔ od47d 矢量精度：落点/停留段 logo 只**缩小**（h 恒 < 起播最大 h0），
        #    清晰档位图（矢量源在 clear_w 处预栅格化、随 dpr 用满物理像素）恒被
        #    **缩小**绘制，边缘恒清晰、零放大；故直接 drawPixmap 已缓存的清晰档
        #    pix，单帧 <1ms（不再每帧 QSvgRenderer 实时重栅格化 —— 那正是 planB
        #    落点段真机偶发卡顿的根因，planA 无落点段、全程走 drawPixmap 故不卡）。
        #    淡入段的高斯仍留在位图域（模糊是逐像素运算），两者不冲突。
        # 真实落点（墨迹左边缘/屏宽、上边缘/屏高、墨迹高/屏高），由
        # splash_anim.set_land_from_window() 注入；没注入就用设计稿近似值。
        self._land: tuple[float, float, float] = (0.0300, 0.0785, 0.0850)
        self._fade_k = 0.0
        self._fade_pix: QPixmap | None = None
        self._blur_released = False
        # 后台预热状态（见 _preheat_async）：worker 把模糊档 QImage 存这里，
        # 主线程 _motion_tick 下一拍再转 QPixmap 填入 _blur_cache。
        self._preheat_pending: dict = {}
        self._preheat_done = False

        # ---- 动态档位（od46）-------------------------------------------
        s = self._spec
        # 任意 t 的 bmp_w = ink_h*lockup_aspect/ink_w_frac，ink_h 最大 = h0*screen_h。
        # 把它算成 bmp_w_max 并让清晰位图宽 ≥ 它 → 绘制永远只缩小不放大。
        bmp_w_max = (max(s.h0, 0.15) * self._screen_h * s.lockup_aspect
                     / s.ink_w_frac)
        self._bmp_w_max = bmp_w_max
        clear_w = math.ceil(bmp_w_max) + 2
        # 极端组合（8K 逻辑分辨率 + 200% DPI）内存安全网：单张 QImage 物理宽
        # 上限 10240px。⛔ 别调小：4K@200% 的 clear_w≈4762 逻辑 → 物理 9524px，
        #    曾误设 8192 把 _clear_w 压到 4096，播放初期反而放大 1.162×
        #    （多机型矩阵实测）。常规机型（含 4K@100%/200%、超宽）永不触发。
        if clear_w * self._dpr > 10240.0:
            clear_w = max(1600, int(10240.0 / self._dpr))
        self._clear_w = clear_w
        # 模糊位图宽：clamp 到 [450,1200] 物理 px（dpr=1.0），钉死高斯成本。
        self._blur_w = max(450, min(1200, int(round(bmp_w_max / 3.0))))
        # 模糊缓存内存预算：全分辨率档约 phys_w*phys_h*4 字节/张。常规机型
        # （1080p@200% ≈20MB/张）拿到全部 13 档；8K 级（≈85MB/张）自动减到
        # 3 档 ≈294MB，绝不 OOM。⛔ 不要只加物理宽上限：那会把清晰档也压糊。
        _phys_w = clear_w * self._dpr
        _phys_h = _phys_w / max(1.0, s.bitmap_aspect)
        _per = _phys_w * _phys_h * 4.0
        self._sigma_n = max(3, min(13, int(_BLUR_CACHE_BUDGET / max(1.0, _per))))
        # 模糊半径：屏幕逻辑空间常量 → 换算成模糊位图空间。
        # sigma_bitmap(step) = step * SIGMA_SCREEN * _blur_w / bmp_w_max
        # 这样不同机型屏幕上模糊半径恒为 18 逻辑 px。

        # 字标渲染缓存按档位预热（13 个模糊档 + 清晰档 + 淡出模糊图）。
        # ⛔ 不再在主线程同步等这 ~90ms 高斯：清晰 logo 同步就绪（淡入起点
        #    sigma→0 直接用它），模糊档丢到后台线程跑；主线程立刻 start 动画
        #    时钟、画第一帧（见 _start_motion）。模糊档由 _motion_tick 下一拍
        #    把 worker 产出的 QImage 转成 QPixmap 填入缓存；未就绪的档由
        #    _blur 按需现场算（同一结果，最多一两帧轻微回退）。画质 / 档数 /
        #    分辨率 / dpr / 帧率**完全不变**。
        try:
            self._preheat_async()
        except Exception:                 # noqa: BLE001 - 任何异常退回原同步逻辑
            try:
                import app_log
                app_log.warning("开屏预热改为后台失败，退回同步预热")
            except Exception:             # noqa: BLE001
                pass
            self._preheat()

    # ---- 素材
    def _pixmap(self, width_logical: int, dpr: float | None = None) -> QPixmap | None:
        d = self._dpr if dpr is None else float(dpr)
        key = (int(width_logical), int(d * 100))
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        try:
            from ui_brand import lockup_pixmap
            pix, _a = lockup_pixmap(self._scheme, float(int(width_logical)), d)
        except Exception:                 # noqa: BLE001
            pix = None
        if pix is None or pix.isNull():
            return None
        self._cache[key] = pix
        return pix

    def _sigma_steps(self) -> tuple[float, ...]:
        """归一化模糊档位（step 1.0→0.0），在 _preheat() 里全部预热。

        常规机型 13 档；极端分辨率按 ``_BLUR_CACHE_BUDGET`` 自适应减少（≥3）。
        """
        n = int(getattr(self, "_sigma_n", 13))
        return tuple(round(1.0 - i / (n - 1), 4) for i in range(n))

    def _sigma_bitmap(self, step: float) -> float:
        """step（0~1）→ 模糊位图空间里的高斯 sigma（屏幕逻辑半径恒定）。"""
        return (step * self._spec.sigma_screen * self._blur_w
                / max(1.0, self._bmp_w_max))

    def _ensure_prep(self):
        """惰性建立预热所需的 4 张 numpy 图（13 档共用，淡入结束一起释放）。

        ⛔⛔ od47 竖线根治：淡入模糊的源图**只能是图标**。完整 lockup 里含
        divider 和文字，真高斯（sigma 最大 18 逻辑 px）会把它们的墨迹晕过
        图标 clip 右缘（那里只退让 1.5px），淡入阶段就显出一条竖影 ——
        用户两次指出「竖线提前出现」，逐像素实测 t=0.70 竖线带 335px 墨迹，
        fillRect 擦右半无效（halo 已渗进 clip 内侧）。故：
          ``icon_*`` = ``x ≥ divider_x`` 右半清零 → 淡入 13 档专用；
          ``full_*``  = 完整 lockup → **只**供淡出图（整个 logo 一起模糊淡出）。
        """
        prep = getattr(self, "_blur_prep", None)
        if prep is not None:
            return prep
        import numpy as np
        base = self._pixmap(self._clear_w)
        if base is None:
            return None
        dpr = base.devicePixelRatioF() or 1.0
        base_img = base.toImage()                # 物理像素（clear_w*dpr）
        phys_w, phys_h = base_img.width(), base_img.height()
        work_w = max(64, int(round(self._blur_w)))
        work_h = max(1, int(round(work_w * phys_h / max(1, phys_w))))
        work_img = base_img.scaled(work_w, work_h,
                                   Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
        base_rgba = base_img.convertToFormat(QImage.Format.Format_RGBA8888)
        base_u8 = np.frombuffer(base_rgba.constBits(), dtype=np.uint8).reshape(
            phys_h, base_rgba.bytesPerLine() // 4, 4)[:, :phys_w, :].copy()
        wi = work_img.convertToFormat(QImage.Format.Format_RGBA8888)
        # ⛔ 用 QImage **实际**尺寸，别用 work_w/work_h：KeepAspectRatio 会
        #    把高度取整成相邻值（8K 级实测 282→281），按算出的值 reshape 会炸。
        w_act, h_act = wi.width(), wi.height()
        work_arr = np.frombuffer(wi.constBits(), dtype=np.uint8).reshape(
            h_act, wi.bytesPerLine() // 4, 4)[:, :w_act, :].copy()
        # 退让分隔线左缘 AA 余量（见 _DIV_CUT_GUARD 注释）——否则那列残影
        # 会被高斯晕成一条「提前出现」的竖直淡带。
        cut_frac = self._spec.divider_x - _DIV_CUT_GUARD
        cut_w = max(0, int(round(w_act * cut_frac)))
        cut_p = max(0, int(round(phys_w * cut_frac)))
        icon_work_arr = work_arr.copy()
        icon_work_arr[:, cut_w:, :] = 0          # divider + 文字区整体透明
        icon_base_u8 = base_u8.copy()
        icon_base_u8[:, cut_p:, :] = 0           # 底噪补偿基准也必须图标-only
        prep = (dpr, phys_w, phys_h, QPixmap.fromImage(work_img),
                base_u8, icon_base_u8, work_arr, icon_work_arr)
        self._blur_prep = prep
        return prep

    def _compose_image(self, small, key: float, base_arr) -> QImage:
        """小图数组 → 全分辨率 QImage（含轻档底噪补偿）。

        ⛔ od47d：全程只用 QImage / numpy —— QImage 是值类型且可重入，
        **可以在线程池里并行**；QPixmap 只能 GUI 线程，留到 ``_compose``。
        实测 13 档合成串行 373ms（占预热 617ms 的大头），并行后大幅下降。
        """
        import numpy as np
        phys_w, phys_h = self._blur_prep[1:3]
        qimg = QImage(np.ascontiguousarray(small).data,
                      small.shape[1], small.shape[0], 4 * small.shape[1],
                      QImage.Format.Format_RGBA8888).copy()
        out = qimg.scaled(phys_w, phys_h,
                          Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
        # ⛔ 底噪补偿（od47）：downscale→upscale 本身是低通，sigma→0 时仍
        #    比清晰档糊（平均差 12.3、6.88% 像素 >64、边缘 max=255），淡入
        #    末帧会硬跳。对 step≤0.25 按 w=step/0.25 与「图标-only 全分辨率
        #    原图」线性混合：step=0 精确 == 清晰档、0.25 == 纯模糊档。
        if key <= 0.25 + 1e-6:
            wi = int(round(min(1.0, key / 0.25) * 255.0))
            if 0 < wi < 255:
                oi = out.convertToFormat(QImage.Format.Format_RGBA8888)
                ou = np.frombuffer(oi.constBits(), dtype=np.uint8).reshape(
                    phys_h, oi.bytesPerLine() // 4, 4)[:, :phys_w, :]
                mixed = ((base_arr.astype(np.uint16) * (255 - wi)
                          + ou.astype(np.uint16) * wi) // 255).astype(np.uint8)
                out = QImage(np.ascontiguousarray(mixed).data, phys_w, phys_h,
                             4 * phys_w, QImage.Format.Format_RGBA8888).copy()
        return out

    def _compose(self, small, key: float, base_arr) -> QPixmap:
        """主线程把并行产出的 QImage 转成 QPixmap（Qt 要求 GUI 线程）。"""
        dpr = self._blur_prep[0]
        out = QPixmap.fromImage(self._compose_image(small, key, base_arr))
        out.setDevicePixelRatio(dpr)
        return out

    def _blur(self, step: float) -> QPixmap | None:
        """全分辨率高斯模糊档（od46 修正：模糊≠降分辨率）。

        ⛔ 旧实现把 lockup 只栅格化成 ``_blur_w``（~794 物理 px）的低位图，再在
        paint 里被拉伸到屏幕实际宽度（dpr=2 时拉到 ~6×）→ 用户看到的「高斯模糊」
        其实绝大部分是**低分辨率放大**的像素糊，不是真模糊；淡入全程发虚、淡出
        也是低位图。这正是用户 2026-11-05 投诉「高斯模糊不是降低分辨率」的根。

        修正：模糊仍在**缩小的分辨率**上算（保住 ~0.3s 预热成本，不引 cv2/scipy），
        但把模糊结果**升回全分辨率**（平滑）再缓存；paint 里 1:1 画出 = 真模糊、
        零放大。downscale→gaussian→upscale 的比例正好吸收 dpr，使屏幕逻辑模糊半径
        恒为 ``step*sigma_screen``（on-screen 与 dpr 无关），``_sigma_bitmap`` 公式不变。
        """
        # 淡入结束后档表已释放：任何迟到的调用一律退清晰档，绝不重算 20MB。
        if self._blur_released:
            return self._pixmap(self._clear_w)
        if step <= 0.001:
            return self._pixmap(self._clear_w)   # sigma→0 即清晰档（无缝交接）
        key = round(float(step), 4)
        hit = self._blur_cache.get(key)
        if hit is not None:
            return hit
        # 后台预热产出的 QImage：主线程转 QPixmap 后入缓存（避免重复现场算）。
        pend = getattr(self, "_preheat_pending", None)
        if pend is not None and key in pend:
            qimg = pend.pop(key)
            pix = QPixmap.fromImage(qimg)
            pix.setDevicePixelRatio(self._dpr)
            self._blur_cache[key] = pix
            return pix
        prep = self._ensure_prep()
        if prep is None:
            return None
        # 命中并行预热的小图数组直接合成；失败则现场用**图标-only 源**补算。
        small = getattr(self, "_small_cache", {}).get(key)
        if small is None:
            small = _gaussian(prep[7], self._sigma_bitmap(key))
        out = self._compose(small, key, prep[5])  # icon_base_u8 作底噪补偿基准
        self._blur_cache[key] = out
        return out

    def _preheat(self) -> None:
        """把播放期要用的位图与模糊档全部先算好（在窗口 show 之前调）。

        播放期再算 = 那一拍必然掉帧，用户看到的就是「卡一下」。
        淡出模糊图 ``_fade_pix`` 也在这里预计算：``set_fade(k)`` 播放期只赋值。

        od46 修正：模糊档一律从**全分辨率清晰位图**经 downscale→gaussian→upscale
        生成（见 ``_blur``），播放期 1:1 画出 = 真模糊、零放大。

        ⛔ od47 并行预热：真高斯生效后 12 档串行要 ~205ms，加上升采样/底噪
        补偿总预热 639ms（全在 show() 之前 = 双击白等）。numpy 卷积释放 GIL，
        用线程池把「12 档小图模糊」并到 ~50ms；QPixmap/QImage 的构造与缩放
        **只在主线程**（Qt 非线程安全）。失败一律降级为逐档现场算，不挡启动。
        """
        base = self._pixmap(self._clear_w)
        if base is None:
            return
        steps = self._sigma_steps()
        self._small_cache = {}
        prep = self._ensure_prep()
        if prep is None:
            return
        dpr = prep[0]
        base_u8, icon_base_u8 = prep[4], prep[5]
        work_arr, icon_work_arr = prep[6], prep[7]
        # ⛔ 淡入各档一律用**图标-only 源**（见 _ensure_prep）；额外一档
        #    「fade」用完整源，供落定后整个 logo 一起高斯模糊淡出。
        jobs = [(round(st, 4), self._sigma_bitmap(st), icon_work_arr,
                 icon_base_u8, st) for st in steps if st > 0.001]
        jobs.append(("fade", self._sigma_bitmap(1.0), work_arr,
                     base_u8, 1.0))
        n_fadein = len(jobs) - 1
        ready = False
        try:
            from concurrent.futures import ThreadPoolExecutor

            def _job(j):
                key, sig, src, barr, ckey = j
                small = _gaussian(src, sig)
                return key, self._compose_image(small, ckey, barr)

            # 高斯 + 升采样 + 底噪补偿整条链都在线程池（QImage 可重入）；
            # 主线程只把产出的 QImage 转成 QPixmap（Qt 要求 GUI 线程）。
            with ThreadPoolExecutor(max_workers=min(6, len(jobs))) as ex:
                for key, qimg in ex.map(_job, jobs):
                    pix = QPixmap.fromImage(qimg)
                    pix.setDevicePixelRatio(dpr)
                    if key == "fade":
                        self._fade_pix = pix
                    else:
                        self._blur_cache[key] = pix
            ready = (len(self._blur_cache) >= n_fadein
                     and self._fade_pix is not None)
        except Exception as exc:              # noqa: BLE001 - 并行失败就逐档现场算
            try:
                import app_log
                app_log.warning(f"开屏并行预热降级：{exc!r}")
            except Exception:                 # noqa: BLE001
                pass
        if ready:
            self._pixmap(self._clear_w)
            return
        # 兜底：任何并行失败都退回原来的逐档现场算，绝不挡启动
        self._blur_cache.clear()
        self._fade_pix = None
        for step in steps:
            self._blur(step)
        self._pixmap(self._clear_w)
        if self._fade_pix is None:
            fade_small = _gaussian(work_arr, self._sigma_bitmap(1.0))
            # ⛔ 淡出必须含文字：完整源图，key=1.0 不触发底噪补偿
            self._fade_pix = self._compose(fade_small, 1.0, base_u8)

    def _preheat_async(self) -> None:
        """后台线程跑 13 档高斯（numpy 释放 GIL，可并行），结果以 QImage 形式
        存进 ``_preheat_pending``；主线程在 ``_motion_tick`` 下一拍把 QImage 转成
        QPixmap 填入 ``_blur_cache`` / ``_fade_pix``。

        ⛔ 这样 ``_start_motion`` 的 ``show()+repaint()`` 之后**不再有主线程长
        阻塞**：动画时钟立刻 start、第一帧立刻画（清晰 logo 已在 ``_preheat_async``
        的同步段就绪，淡入起点 sigma→0 直接用）。模糊档在后台逐渐填满，未就绪的
        档由 ``_blur`` 按需现场算，结果完全一致，最多一两帧轻微回退（~几 ms），
        不会卡、不会定格。画质 / 档数 / 分辨率 / dpr / 帧率**完全不变**。
        """
        base = self._pixmap(self._clear_w)
        if base is None:
            return
        prep = self._ensure_prep()
        if prep is None:
            return
        base_u8, icon_base_u8 = prep[4], prep[5]
        work_arr, icon_work_arr = prep[6], prep[7]
        steps = self._sigma_steps()
        jobs = [(round(st, 4), self._sigma_bitmap(st), icon_work_arr,
                 icon_base_u8, st) for st in steps if st > 0.001]
        jobs.append(("fade", self._sigma_bitmap(1.0), work_arr, base_u8, 1.0))
        self._preheat_pending = {}
        self._preheat_done = False
        import threading

        def _run() -> None:
            try:
                for key, sig, src, barr, ckey in jobs:
                    small = _gaussian(src, sig)
                    qimg = self._compose_image(small, ckey, barr)
                    self._preheat_pending[key] = qimg
                self._preheat_done = True
            except Exception:             # noqa: BLE001 - 后台失败由 _blur 兜底
                pass

        threading.Thread(target=_run, name="splash-blur-preheat",
                         daemon=True).start()

    def _absorb_pending(self) -> bool:
        """主线程把后台预热的 QImage 转成 QPixmap 填入缓存（QPixmap 只能在
        GUI 线程构造）。每帧由 ``_motion_tick`` 调用；返回后台是否全部完成。
        """
        pend = getattr(self, "_preheat_pending", None)
        if not pend:
            return getattr(self, "_preheat_done", False)
        for _k, _q in list(pend.items()):
            _p = QPixmap.fromImage(_q)
            _p.setDevicePixelRatio(self._dpr)
            if _k == "fade":
                self._fade_pix = _p
            else:
                self._blur_cache[_k] = _p
        pend.clear()
        return getattr(self, "_preheat_done", False)

    def release_blur_cache(self) -> None:
        """淡入结束后释放模糊档表（约 244MB @1080p@200%），幂等。

        ``_fade_pix`` 已单独持有 ``_blur(1.0)`` 的 QPixmap 引用，dict.clear()
        不会把它一起回收；之后 ``_blur()`` 一律退清晰档，播放期零重渲染。
        """
        if self._blur_released:
            return
        self._blur_released = True
        self._blur_cache.clear()
        self._blur_prep = None
        self._small_cache = {}

    # ---- 外部注入
    def set_land(self, xr: float, yr: float, hr: float) -> None:
        """注入主窗页头 logo 的真实墨迹几何（归一化）。"""
        self._land = (float(xr), float(yr), float(hr))

    def set_fade(self, k: float) -> None:
        """淡出进度 0→1：只赋值。模糊版已由 _preheat() 预计算，播放期零重渲染。"""
        self._fade_k = max(0.0, min(1.0, float(k)))

    # ---- 状态
    def _state(self, t: float,
               asp: float = 1.6) -> tuple[float, float, float, float, float, float]:
        """返回 (x, y, h, clip, sigma_ratio, alpha)。

        ⛔ 单位约定（混用会直接偏位）：``x`` 是**相对屏宽**，``y``/``h`` 是
        **相对屏高**。长度（logo 宽）本身是「相对屏高的宽度」，要换算成 x 的
        基准必须再除以 ``asp = 屏宽/屏高`` —— 第一版忘了除，logo 就偏左 1.6 倍
        （用户 2026-11-04：「图标 logo 首先是居中的」）。
        """
        s = self._spec
        lx, ly, lh = self._land
        # 淡入期图标放大 10% 的「呼吸」脉冲：t_blank_end 前=1.1×，随淡入进度
        # 收回 1.0×（与左移缩小段起点 h0 严丝合缝衔接，无尺寸跳变）。blank 段
        # alpha=0 不可见，脉冲不影响观感，仅让图标「淡入即落定」更有呼吸感。
        kf = min(1.0, max(0.0, _seg(t, s.t_blank_end, s.t_fade_end)))
        _h_pulse = 1.0 + 0.10 * (1.0 - kf)
        # 起点：图标水平垂直都居中（含脉冲尺寸）
        h_start = s.h0 * _h_pulse
        w_h = h_start * s.lockup_aspect * s.text_clip0     # 可见宽（相对屏高）
        y_start = 0.5 - h_start / 2.0
        x_start = 0.5 - (w_h / max(0.1, asp)) / 2.0

        if t < s.t_blank_end:
            return (x_start, y_start, h_start, s.text_clip0, 1.0, 0.0)

        if t < s.t_fade_end:
            # 模糊淡入：alpha 用 ease-out，sigma 用 ease-in（先糊后清）
            k = _seg(t, s.t_blank_end, s.t_fade_end)
            return (x_start, y_start, h_start, s.text_clip0,
                    (1.0 - _ease_in_cubic(k)), _ease_out_cubic(k))

        if t < s.t_mark_end:
            # 左移 + 缩小（非线性）
            k = _ease_in_out_cubic(_seg(t, s.t_fade_end, s.t_mark_end))
            h = s.h0 + (s.h1 - s.h0) * k
            x = x_start + (s.x1 - x_start) * k
            y = 0.5 - h / 2.0
            return (x, y, h, s.text_clip0, 0.0, 1.0)

        if t < s.t_text_end:
            # 文字从左往右逐渐显露（clip text_clip0≈0.448 → 1.0），位置/尺寸保持
            k = _ease_in_out_cubic(_seg(t, s.t_mark_end, s.t_text_end))
            h = s.h1
            clip = s.text_clip0 + (1.0 - s.text_clip0) * k
            return (s.x1, 0.5 - h / 2.0, h, clip, 0.0, 1.0)

        if t < s.t_hold_end:
            return (s.x1, 0.5 - s.h1 / 2.0, s.h1, 1.0, 0.0, 1.0)

        # 落点段（仅 planB）：整体缩小 + 上移左移（_ease_land 先慢后快再慢、
        # 长尾临界阻尼、无过冲），落向主窗页头角落，与 brand_header 几何无缝衔接。
        if s.land:
            k = _ease_land(_seg(t, s.t_hold_end, s.t_land_end))
            h = s.h1 + (lh - s.h1) * k
            x = s.x1 + (lx - s.x1) * k
            y = (0.5 - s.h1 / 2.0) + (ly - (0.5 - s.h1 / 2.0)) * k
            return (x, y, h, 1.0, 0.0, 1.0)
        # planA：无「向左上角落点」，停在「左移缩小」后的最终位置，随后静止等淡出。
        return (s.x1, 0.5 - s.h1 / 2.0, s.h1, 1.0, 0.0, 1.0)

    # ---- 绘制
    def paint(self, painter: QPainter, w: int, h: int, t: float) -> None:
        s = self._spec
        # 高斯模糊淡出：logo 由清晰→全模糊（_blur_w 交叉溶入 _fade_pix），整体再
        # 随 _out_a 淡到透明。**窗口本身不调 setWindowOpacity**（frameless 窗在
        # opacity→0 撤场时会触发 Windows DWM 闪一下），改为只淡内容；窗口置
        # WA_TranslucentBackground，内容全透明后 close() 即可无缝露出主窗，无闪。
        # 两段错开：先模糊（k∈[0,0.65]）再淡出（k∈[0.35,1.0]），重叠区让模糊在还
        # 可见时已成形，不会「没看清就消失」，高品质高斯才看得清。
        fk = self._fade_k
        _style = str(getattr(s, "fade_style", "all") or "all")
        _blur_step = 0.0
        if fk <= 0.001:
            _blur_w = 0.0
            _out_a = 1.0
            # ⛔⛔ 落点全程**底色保持不透明**（2026-11-06 用户：「到位后再淡出啊」）。
            #   别再让底色跟着 _ease_land 一起收口（od51 干过，被用户明确否掉）：
            #   logo 还在飞、幕布同时化开 = 两层一起变，是「溶解」不是物理运动。
            #   正确次序：logo 在自己幕布上飞到位 → 落定后才整体淡出（见 fk>0 分支，
            #   由 finish() 在 _motion_total 之后才启动，_FADE_MS=300）。
            #   为什么「不透明幕布」不会露出边界：开屏底色 === 主窗 @bg@
            #   （splash_anim:342 / ui_theme token "bg" 同为 #0D0D0F、浅色 #FFFFFF），
            #   落点区域（页头左上角）在主窗那边也是同一块纯色背景 → 幕布与主窗
            #   同色同值，视觉上本来就是一个连续面；唯一的边界风险是 logo 落点没
            #   对准，那个由 set_land_from_window 的真几何注入保证。
            _bg_a = 1.0
        elif _style == "bg":
            # ── planB「背景高斯模糊淡出」（2026-11-07 用户点名）──────────────
            # lockup **全程保持清晰**，只有幕布糊散褪去：幕布做径向溶解（外圈先化、
            # 中心后化），视觉上就是"清晰的主体浮在正在化开的底上"，最后一起消失。
            # ⛔ 不要给 lockup 加模糊：那会变成 planA 的效果，两套方案又长一样。
            _bg_a = 1.0 - _smoothstep(_seg(fk, 0.0, 0.88))
            _blur_w = 0.0
            _out_a = 1.0 - _smoothstep(_seg(fk, 0.74, 1.0))
        else:
            # ── planA「整体高斯模糊淡出」────────────────────────────────────
            # lockup 从清晰**逐档糊开**（0→最大档），幕布**晚一点**再褪（0.35 起），
            # 这样"糊"这个过程是看得见的；最后一起淡到透明。
            _bg_a = 1.0 - _smoothstep(_seg(fk, 0.35, 1.0))
            _blur_step = _smoothstep(_seg(fk, 0.0, 0.90))
            # ⛔⛔ 2026-11-07 用户点名：planA 淡出要「整 logo（图标+文字）一起高斯模糊」，
            #    不能只糊图标。旧实现在此把 pix 替换成 self._blur(_blur_step) —— 那张表是
            #    淡入专用的「图标-only」源（文字区在 _ensure_prep 里被清零），且
            #    release_blur_cache 后只退清晰档 → 实际文字丢/什么都不糊。
            #    改法：pix 保持「清晰整图」，真正的模糊交给下方 _blur_w 交叉溶 _fade_pix
            #    （整图最大高斯、含文字，且独立于 _blur_cache、release 后仍保留）：
            #    _blur_w=_blur_step 从 0→1，整 logo 连续糊开，文字随图标一起糊。
            _blur_w = _blur_step
            _out_a = 1.0 - _smoothstep(_seg(fk, 0.62, 1.0))
        _bga = int(round(255.0 * max(0.0, min(1.0, _bg_a))))
        if _style == "bg" and fk > 0.001 and _bga > 0:
            # planB「背景高斯模糊淡出」：纯色幕布做**径向溶解** —— 外圈先化开、
            # 中心最后化，读起来是"背景从四周糊散"，而不是整块均匀褪色。
            # ⛔ 幕布是纯色，真高斯算不出任何差别；这里用径向 alpha 梯度来表达
            #    「化开」的方向性（圆心落在 lockup 的墨迹中心）。
            _st = self._state(t, w / max(1, h))
            _cx = float(w) * (_st[0] + s.ink_w_frac * 0.5)
            _cy = float(h) * (_st[1] + s.ink_h_frac * 0.5)
            _rad = max(w, h) * 0.78
            grad = QRadialGradient(_cx, _cy, _rad)
            _edge = int(round(_bga * max(0.0, 1.0 - 1.05 * fk)))
            _c = self._bg
            grad.setColorAt(0.0, QColor(_c.red(), _c.green(), _c.blue(), _bga))
            grad.setColorAt(0.55, QColor(_c.red(), _c.green(), _c.blue(),
                                         int(_bga * 0.92)))
            grad.setColorAt(1.0, QColor(_c.red(), _c.green(), _c.blue(), _edge))
            painter.fillRect(0, 0, w, h, grad)
        else:
            painter.fillRect(0, 0, w, h,
                             QColor(self._bg.red(), self._bg.green(),
                                    self._bg.blue(), _bga))
        if w <= 0 or h <= 0:
            return
        xr, yr, hr, clip, sigma_ratio, alpha = self._state(t, w / max(1, h))
        if alpha <= 0.001:
            return

        ink_h = hr * h
        # 位图尺寸按**完整** lockup 墨迹反推；clip 只决定露出多少
        bmp_w = ink_h * s.lockup_aspect / s.ink_w_frac
        bmp_h = bmp_w / s.bitmap_aspect
        left = xr * w - s.ink_left * bmp_w
        top = yr * h - s.ink_top * bmp_h

        if sigma_ratio > 0.02:
            # 就近取已预热的档位（step 空间）
            steps = self._sigma_steps()
            nearest = min(steps, key=lambda st: abs(st - sigma_ratio))
            pix = self._blur(nearest)
        else:
            pix = self._pixmap(self._clear_w)
        # ⛔⛔ 2026-11-07 用户点名（第二次）：「planA 的最后整体高斯模糊淡出是整个
        #    logo 都要模糊，不只是图标，文字也要」。旧实现在此把 pix 替换成
        #    ``self._blur(_blur_step)`` —— 那是**淡入专用、图标-only 源**的档表
        #    （_preheat 的 jobs 前 N 项用 icon_work_arr / icon_base_u8，文字区已被
        #    清零，见 _ensure_prep）。于是只要 _blur_step>0.01，文字就整段消失，
        #    只剩图标在糊 —— 用户看到的正是「文字先没了、图标还在」。
        #    改法：**不再用档表替换 pix**，pix 全程保持「清晰整图（图标+文字）」，
        #    真正的逐步变糊交给下面 _blur_w=_blur_step 交叉溶入 ``_fade_pix``
        #    （_preheat 里 key=="fade" 那一档用的是**完整源** work_arr/base_u8，
        #    含文字）。清晰整图 → 全糊整图的连续交叉 = 整 logo 一起糊开，文字不丢。
        if pix is None:
            return

        base_a = alpha * (1.0 - _blur_w) * _out_a
        target = QRectF(left, top, bmp_w, bmp_h)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if clip < 0.999:
            # 三段画：图标 / 竖线（SVG 原图，从中央向上下展开）/ 文字。
            # 文字从竖线**右边缘**起算，所以永远不会压到图标上。
            # ⛔⛔ 图标 clip 必须**退到 divider 左侧之外**（实测打样咬出来的坑）：
            #    以前写 ``icon_w + 1.0`` 会把 SVG 原图 divider 的最左 1px 连同
            #    **完整高度**一起画出来 —— t=0.70 就有一条 180px 的竖线，且全程
            #    都在，于是「从中央向上下展开」只剩宽度 1px→4px 的变化，肉眼
            #    完全看不出（_check/_measure_shots.py 逐帧像素统计实锤）。
            #    od46 动态档位改造后这三处偏移必须**原样保持**，不得回退。
            icon_w = bmp_w * (s.divider_x - _DIV_CUT_GUARD)
            painter.save()
            painter.setClipRect(QRectF(left, top, icon_w, bmp_h))
            painter.setOpacity(base_a)
            painter.drawPixmap(target, pix, QRectF(pix.rect()))
            painter.restore()
            # ⛔⛔ 竖线提前出现的根治在渲染**源图**：淡入模糊档已改成
            #    「图标-only」源（_ensure_prep 里把 divider/文字区清零后再高斯），
            #    halo 根本不存在；这里不要再 fillRect 擦，否则会把图标右缘的
            #    自然渐隐削出生硬边。详见 _ensure_prep 注释。
            # 竖线：clip 到 divider 所在的那一窄条，高度按进度**从中心向两端**
            # 长出来（用户 2026-11-04：「从竖线的中央向上下两边展开」0.2s）。
            line_k = _ease_out_cubic(_seg(t, s.t_mark_end,
                                          s.t_mark_end + s.t_line_ms))
            if line_k > 0.01:
                # 左边界跟着图标 clip 一起退 1.5px、宽度补回来，保证展开时
                # 图标↔竖线↔文字三段无缝，又不把 divider 提前漏出去。
                dx = left + s.divider_x * bmp_w - 1.5
                dy = top + s.divider_y * bmp_h
                dw = s.divider_w * bmp_w + 2.5
                dh_full = s.divider_h * bmp_h
                dh = dh_full * line_k
                painter.save()
                painter.setClipRect(QRectF(dx, dy + (dh_full - dh) / 2.0,
                                           dw, max(1.0, dh)))
                painter.setOpacity(base_a)
                painter.drawPixmap(target, pix, QRectF(pix.rect()))
                painter.restore()
            # 文字：从竖线右边缘开始向右摊开
            text_shown = bmp_w * (clip - s.text_clip0)
            if text_shown > 1.0:
                # ⛔ od47g（用户 22:04「文字出现设计不够高级」）：硬 clip 横扫
                #    读起来很「cheap」。改成「浮现」式：① 前缘羽化溶进背景、
                #    ② 整体轻微淡入包络，二者叠加 = 文字像从雾里渐渐显形，
                #    而不是被一把刀切开。背景是纯色 _bg，羽化用同色渐变即可。
                clip_l = left + s.text_clip0 * bmp_w + 2.0
                clip_r = QRectF(clip_l, top, text_shown, bmp_h)
                # ① 淡入包络：文字段前 ~25% 从 0 淡到 1，避免一上来满不透明地
                #    「啪」出现（与竖线 line_k 同源节奏，整段协调）。
                fk_text_in = _ease_out_cubic(min(
                    1.0, _seg(t, s.t_mark_end,
                              s.t_mark_end + s.t_line_ms * 1.5)))
                painter.save()
                painter.setClipRect(clip_r)
                painter.setOpacity(base_a * fk_text_in)
                painter.drawPixmap(target, pix, QRectF(pix.rect()))
                painter.restore()
                # ② 前缘羽化：在揭示区最右 ~3% 宽盖一条「左透明→右 _bg 不透明」
                #    的渐变，把硬边溶进背景；只碰最右那一条，不动已显形的文字。
                feather = max(4.0, bmp_w * 0.03)
                fg = QLinearGradient(
                    clip_r.x() + clip_r.width() - feather, 0.0,
                    clip_r.x() + clip_r.width(), 0.0)
                bgc = self._bg
                fg.setColorAt(0.0, QColor(bgc.red(), bgc.green(),
                                         bgc.blue(), 0))
                fg.setColorAt(1.0, QColor(bgc.red(), bgc.green(),
                                         bgc.blue(), 255))
                painter.save()
                painter.setOpacity(base_a * fk_text_in)
                painter.fillRect(clip_r.x() + clip_r.width() - feather,
                                top, feather, bmp_h, fg)
                painter.restore()
        else:
            painter.setOpacity(base_a)
            # ⛔ planB 落点/停留段（t>=t_hold_end）原本每帧 self._svg.render()
            #    实时重栅格化整个 lockup（每帧目标尺寸都变），是真机落点段偶发卡顿
            #    的根因（planA 无落点段、整段走 drawPixmap 故不卡）。落点段 logo 只
            #    **缩小**（h 0.21→0.085，恒 < t_hold_end 之前的最大 h0），清晰档位图
            #    恒被**缩小**绘制 = 矢量源在 clear_w 处预先栅格化的高位图降采样，
            #    边缘恒清晰、零放大。故直接画已缓存的清晰档 pix（与 SVG 同源、
            #    分辨率更高），drawPixmap 缩放开销可忽略（<1ms，远低于 SVG 重栅格）。
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(target, pix, QRectF(pix.rect()))
        # 淡出：高斯模糊版交叉溶入（背景模糊淡出）。模糊图已在 _preheat 算好。
        if _blur_w > 0.01 and self._fade_pix is not None:
            painter.setOpacity(alpha * _blur_w * _out_a)
            painter.drawPixmap(target, self._fade_pix,
                               QRectF(self._fade_pix.rect()))
        painter.setOpacity(1.0)
