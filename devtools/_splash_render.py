"""车队开屏动画 —— 4K60 逐帧合成 + 编码。

两个收尾方案，前半段（淡入 → 图标左移 → 分隔线 → 文字挪出）完全相同：

  A 版：定版停一拍 → 整幅高斯模糊淡出，末帧回到纯底色。
  B 版：定版停一拍 → 整体向左上角收起并缩小、途中焦点回归（先微虚后清晰），
        落到软件 UI 品牌页头队标的位置与尺寸，停在那一帧交给主窗口。

浅底 / 深底两版共用同一套时间轴与几何，仅换墨色。

用法：
    python devtools/_splash_render.py probe [A|B]
    python devtools/_splash_render.py render [A|B] [light|dark]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTERS = os.path.join(ROOT, "_check", "splash", "masters")
PROBE = os.path.join(ROOT, "_check", "splash", "probe")
OUTDIR = os.path.join(ROOT, "assets", "splash")

W, H = 3840, 2160
FPS = 60

# —— 方案表（秒）——
PLANS = {
    # out = 整幅淡出窗口；fly = 向左上角收起的窗口；dur = 总时长
    "A": {"dur": 3.80, "out": (2.95, 3.75), "fly": None},
    "B": {"dur": 3.70, "out": None, "fly": (2.47, 3.52)},
}

# 开场在淡入之前先停一拍**纯底色**（用户 2026-11-03：「淡入之前留个 0.3s」）。
# 实现 = 整条时间轴统一右移 LEAD：lead 期间画面就是第 0 帧（纯底、无任何内容），
# 淡入/左移/揭示/收尾全部顺延，总时长相应 +LEAD。
LEAD = 0.30

# —— 前半段时间轴（两版共用）——
# 淡入与左移之间留 0.10s「落定」：太短显得忙、太长显得顿 —— 这是非线性的呼吸位。
T_IN = 0.84                      # 整幅模糊淡入
T_MOVE = (0.94, 1.80)            # 图标左移+缩小（起点从 1.00 前提到 0.94，压缩死区）
T_DIV_DUR = 0.26                 # 分隔线长出
T_ZH_DUR = 0.60                  # 中文横扫
T_EN_DUR = 0.50                  # 英文横扫
T_ZH_LAG = 0.06                  # 中文相对分隔线的滞后
T_EN_LAG = 0.10                  # 英文相对中文的滞后

FEATHER = 105                    # 文字软边（px）
PAD = 96                         # 模糊留白（≥ 最大半径）
# 开场淡入：⛔ 2026-11-03 用户先要「空白时间压缩到 0」，随后明确「最开始显示的
# logo 要从无到有淡入」—— 现在 op 从 **0** 起步（第 0 帧 = 纯底色，字面意义的"无"），
# 但给不透明度一条**独立的快速通道** OP_IN_DUR：0→1 只在 0.3s 内补满（旧版是
# 跟着整段 0.84s 的 smoothstep 慢慢爬，头 0.2s 几乎看不见 = 空窗感）。
# 虚化仍用整段 T_IN 收实（焦点由虚到实），两个通道叠加 = 从无到有、快速成形、不留空窗。
BLUR_IN = 44.0
OP_IN_DUR = 0.30
BLUR_OUT = 62.0
BLUR_FLY = 0.0                   # ⛔ B 版不做飞行虚化（用户 2026-11-03：直接移动落位）
SCALE_IN = 0.045                 # 淡入时的轻微推近

# B 版落点：与软件 UI 左上角品牌页头的队标**逐像素对齐**（2026-11-03 实机探针测定，
# 见 devtools/_splash_align_probe.py：真实 MainWindow（WA_DontShowOnScreen + showMaximized）
# 量出 logo 盒 逻辑(33,56,560x131.5)，SVG viewBox 1576x370 内衬墨迹 bbox (44,65)-(1535.5,300)
# → 墨迹 逻辑(48.63,79.10,529.97x83.50)；屏幕 3200x2000@200%，视频 3840x2160 按 cover
# 铺满（scale=0.92593，水平裁 177.8）反推即下面三个值。墨迹宽高比与母版一致（差 0.18%）。
# ⛔ 换分辨率 / 缩放比例这三点要重算（重跑 _splash_ui_truth.py 即可）。
# 真值由 _land_ink_4k() 从 ui_truth.json（BrandHeader 实际 lockup 位图的屏幕物理
# 矩形 + 墨迹 bbox）精确反推，消掉手工取整误差与近似假设。下面三个常量仅作
# ui_truth.json 缺失时的回退。
LAND_X, LAND_Y, LAND_W = 297.1, 170.8, 1144.7


def _land_ink_4k(variant: str) -> tuple[float, float, float, float]:
    """UI logo **墨迹盒**在 4K 视频坐标里的精确 (x, y, w, h)。

    与机型无关：只描述「这支视频里 logo 墨迹飞到哪个位置」。视频墨迹盒与 UI 墨迹盒
    同源同素材、宽高比一致（均 = 1060/168），播放器运行时算一个相似变换即可把四角
    精确对齐到任意机型的真实 UI 墨迹盒（见 splash_anim._correction_transform）。

    数据来自 ``ui_truth.json``（``_splash_ui_truth.py`` 生成）：直接取 BrandHeader
    实际 drawPixmap 的那张 lockup 矢量位图，量出墨迹 bbox 与屏幕物理盒，再按 cover
    反推。视频墨迹盒与 UI 墨迹盒用同一组真值，宽高比天然相等 → 相似变换零残差。
    """
    path = os.path.join(MASTERS, "ui_truth.json")
    ink_w, ink_h = 1060.0, 168.0
    if not os.path.isfile(path):
        return (LAND_X, LAND_Y, LAND_W, LAND_W * ink_h / ink_w)
    with open(path, encoding="utf-8") as fh:
        sc = json.load(fh)["schemes"][variant]
    px, py, _pw, _ph = sc["logo_box_physical"]
    ix0, iy0, ix1, iy1 = sc["ink_local"]
    scale, ox, oy = sc["video_scale"], sc["video_ox"], sc["video_oy"]
    return ((px + ix0 - ox) / scale, (py + iy0 - oy) / scale,
            (ix1 - ix0) / scale, (iy1 - iy0) / scale)


def land_box_for(variant: str) -> list[float]:
    """UI logo **墨迹盒**在 4K 视频坐标里的归一化 ``[x0, y0, w, h] / [W, H]``。

    ⛔ 与机型无关：只描述「这支视频里 logo 墨迹飞到哪个位置」，渲染时定死，跟机器
    无关。播放器运行时按真实屏幕 + 真实 UI 墨迹盒算相似变换，把视频墨迹盒拉到真实
    墨迹盒 —— **任意机型**精确贴合（见 splash_anim._correction_transform）。

    ⛔ 必须用**墨迹盒**而非整张 lockup 内容盒：视频墨迹与 UI 墨迹同源、宽高比一致
    （均 = 1060/168），单一相似变换（统一缩放 + 平移）即可四角对齐；若用整张内容盒，
    两者宽高比不同，相似变换只能对上宽度、高度必偏——这正是早期「本机完美、换机偏」
    的根因。
    """
    x, y, w, h = _land_ink_4k(variant)
    return [x / W, y / H, w / W, h / H]


def _write_land_box(variant: str, box: list[float]) -> None:
    """把各变体的归一化落点累积写进 ``assets/splash/_land_box.json``（机型无关）。"""
    path = os.path.join(OUTDIR, "_land_box.json")
    data = {}
    if os.path.isfile(path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:                 # noqa: BLE001
            data = {}
    key = "%s_planb" % variant
    data[key] = [round(v, 8) for v in box]
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                         encoding="utf-8")

DIV_COLOR = {"light": (18, 34, 53), "dark": (245, 247, 248)}
# ⛔ 底色必须与 ui_theme 的页面底一致：收尾要靠开屏窗口 opacity 1→0 让软件背景显出来，
#    两边同色这一次淡出才不会在中间泛出一层灰（浅 #FFFFFF / 深 #0D0D0F）。
BG = {"light": (255, 255, 255), "dark": (13, 13, 15)}


# ---------------------------------------------------------------- 缓动
def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def out_cubic(p: float) -> float:
    return 1.0 - (1.0 - p) ** 3


def out_quint(p: float) -> float:
    return 1.0 - (1.0 - p) ** 5


def in_out_cubic(p: float) -> float:
    return 4.0 * p ** 3 if p < 0.5 else 1.0 - ((-2.0 * p + 2.0) ** 3) / 2.0


def bezier(x1: float, y1: float, x2: float, y2: float):
    """CSS 同款 cubic-bezier 缓动。x 用二分求参数、再取 y，够用且单调。"""
    def ease(p: float) -> float:
        p = clamp01(p)
        lo, hi = 0.0, 1.0
        for _ in range(24):
            mid = (lo + hi) / 2.0
            bx = 3.0 * (1 - mid) ** 2 * mid * x1 + 3.0 * (1 - mid) * mid * mid * x2 + mid ** 3
            if bx < p:
                lo = mid
            else:
                hi = mid
        t = (lo + hi) / 2.0
        return 3.0 * (1 - t) ** 2 * t * y1 + 3.0 * (1 - t) * t * t * y2 + t ** 3
    return ease


# ⛔ 非线性运动的核心：起/终点都给「非零斜率」，速度呈钟形（起步即有轻微动势 → 中段
# 加速 → 落位软收），全程没有"死起点/死终点"。原来的 bezier(0.34,0,0.20,1) 与
# in_out_cubic 在 p=0 处斜率为 0，头 ~0.15s 几乎不动、随后猛冲，就是"先顿一下再走"
# 的来源。这里 y1/y2 取小正值，等于在一条微微倾斜的底线上叠缓动，速度连续不突变。
MOVE_EASE = bezier(0.22, 0.04, 0.22, 1.0)
# 文字揭示同样给非零起点斜率：字随图标尾流"挪"出，而不是从静止被擦出来，避免二次顿挫。
WIPE_EASE = bezier(0.20, 0.03, 0.20, 1.0)

# B 版飞行收尾专用缓动：终点是「定格在角落的 logo」，硬刹停会完全暴露成"卡一下"。
# bezier(0.38,0.08,0.30,1)（2026-11-03 候选扫描选定）：起步首帧 ~5px（无 snap、
# 高于 1px 量化阈值），加速/减速全程单调，峰值 ~47px/帧在第 21 帧（节奏与旧版一致），
# 尾段 1.1/0.6/0.2 干净软收。旧版 .28/.16/.55 首帧 13px = 静止一拍后突然弹走。
FLY_EASE = bezier(0.38, 0.08, 0.30, 1.0)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def seg(t: float, t0: float, t1: float) -> float:
    return clamp01((t - t0) / (t1 - t0) if t1 > t0 else 1.0)


def smoothstep(e0: float, e1: float, x: float) -> float:
    t = clamp01((x - e0) / (e1 - e0) if e1 > e0 else 1.0)
    return t * t * (3.0 - 2.0 * t)


# ---------------------------------------------------------------- 场景
class Scene:
    def __init__(self, variant: str, plan: str = "A") -> None:
        self.variant = variant
        self.plan_name = plan
        self.plan = PLANS[plan]
        self.lead = LEAD
        self.dur = float(self.plan["dur"]) + self.lead   # 开场纯底一拍计入总时长
        self.nframes = int(round(self.dur * FPS))
        with open(os.path.join(MASTERS, "layout.json"), encoding="utf-8") as fh:
            lay = json.load(fh)
        self.lay = lay
        k = lay["k"]
        self.k = k
        self.hero_w = float(lay.get("hero_w", 1900))
        fv = lay["full_vb"]
        self.ox = (W - fv[2] * k) / 2 - fv[0] * k
        self.oy = (H - fv[3] * k) / 2 - fv[1] * k
        self.bg = BG[variant]

        mv = lay["mark_vb"]
        self.mark_vb = mv
        src = Image.open(os.path.join(MASTERS, "mark_%s.png" % variant)).convert("RGBA")
        self.mark_src = src
        self.mark_w = mv[2] * k
        self.mark_h = mv[3] * k
        self.mark_cx = self.mx(mv[0] + mv[2] / 2)
        self.mark_cy = self.my(mv[1] + mv[3] / 2)
        self.mark_ar = src.height / src.width

        tv = lay["text_vb"]
        self.text_vb = tv
        tw, th = lay["text_sprite_w"], int(round(tv[3] * k))
        text = Image.open(os.path.join(MASTERS, "text_%s.png" % variant)).convert("RGBA")
        text = text.resize((tw, th), Image.Resampling.LANCZOS, reducing_gap=3.0)
        self.text_x = self.mx(tv[0])
        self.text_y = self.my(tv[1])
        self.text_w = tw

        rows = lay["text_rows"]
        top = tv[1]
        scale = th / tv[3]
        r0 = int(round((rows[0][0] - top) * scale)) - 1
        r1 = int(round((rows[0][1] - top) * scale)) + 1
        r2 = int(round((rows[1][0] - top) * scale)) - 1
        r3 = int(round((rows[1][1] - top) * scale)) + 1
        self.zh = text.crop((0, max(0, r0), tw, min(th, r1)))
        self.en = text.crop((0, max(0, r2), tw, min(th, r3)))
        self.zh_dy = max(0, r0)
        self.en_dy = max(0, r2)

        dv = lay["divider"]
        self.div_x0 = self.mx(dv[0])
        self.div_x1 = self.mx(dv[0] + dv[2])
        self.div_y0 = self.my(dv[1])
        self.div_y1 = self.my(dv[1] + dv[3])
        self.div_rgb = DIV_COLOR[variant]

        # 图标右缘清空文字区/分隔线的时刻（揭示不得早于此刻，否则字会从图标镂空里透出）
        self.t_div = self._clear_time(self.div_x0 - 2)
        self.t_zh = self._clear_time(self.mx(lay["full_vb"][0] + (737 - 44)) - 2)

        # —— B 版用：起点矩形 + 飞行精灵（落点矩形在精灵生成后精确反推）——
        self.rect0 = (self.mx(fv[0]), self.my(fv[1]), fv[2] * k, fv[3] * k)
        self.rect1 = (LAND_X, LAND_Y, LAND_W, LAND_W * fv[3] / fv[2])  # 占位，A 版不用
        # 飞行精灵：用「揭示段同一套贴图逻辑」预先合成的落定帧（不透明、底色填充）。
        # ⛔ 不能直接用 ref 母版：mark/text 各自从高倍母版缩放而来，与整图重渲染存在
        #    亚像素差异（实测交接帧 1.57% 像素差>8 = 交接瞬间整条 lockup 闪一下，
        #    就是"卡卡的"的另一来源）。同一套合成内容做精灵 → 交接两帧逐像素一致。
        #    飞行期整幅无虚化（BLUR_FLY=0）且背景为纯色，不透明精灵直接盖贴即可。
        if self.plan["fly"] is not None:
            canvas = Image.new("RGB", (W + 2 * PAD, H + 2 * PAD), self.bg)
            self._paste_text(canvas, self.zh, self.zh_dy, 1.0)
            self._paste_text(canvas, self.en, self.en_dy, 1.0)
            self._paste_divider(canvas, 1.0)
            cx, cy, mw = self.mark_state(self.plan["fly"][0] - 1e-3)
            self._paste_mark(canvas, cx, cy, mw, 1.0)
            fx, fy = int(self.rect0[0]), int(self.rect0[1])
            fx1 = int(self.rect0[0] + self.rect0[2]) + 1
            fy1 = int(self.rect0[1] + self.rect0[3]) + 1
            self.fly_sprite = canvas.crop((fx + PAD, fy + PAD, fx1 + PAD, fy1 + PAD))
            self.rect0 = (float(fx), float(fy), float(fx1 - fx), float(fy1 - fy))
            # ⛔ 2026-11-04「要完美重合」：落点不再按「full_vb 扣 4 单位 ≈ 墨迹宽」
            #    估算（那个近似把精灵内墨迹的真实位置/宽度猜偏了 0.2~1px）。直接
            #    量这张精灵里墨迹的**真实 bbox**，令飞行缩放后它精确压在 UI 真值
            #    墨迹上；交棒帧再换成 UI 同源位图，两层零错位、零尺寸差。
            arr = np.asarray(self.fly_sprite, dtype=np.int16)
            diff = np.abs(arr - np.asarray(self.bg, dtype=np.int16)).max(axis=2)
            ys, xs = np.where(diff > 12)
            ix0, iy0 = int(xs.min()), int(ys.min())
            iw = int(xs.max()) + 1 - ix0
            lx, ly, lw, _lh = _land_ink_4k(self.variant)
            s = lw / float(iw)
            self.rect1 = (lx - ix0 * s, ly - iy0 * s,
                          self.rect0[2] * s, self.rect0[3] * s)

    def mx(self, x: float) -> float:
        return self.ox + x * self.k

    def my(self, y: float) -> float:
        return self.oy + y * self.k

    # 某一时刻图标的「位置 + 宽度」
    def mark_state(self, t: float) -> tuple[float, float, float]:
        p_in = seg(t, 0.0, T_IN)
        hero_w = self.hero_w * (1.0 + SCALE_IN * (1.0 - out_quint(p_in)))
        p_move = seg(t, T_MOVE[0], T_MOVE[1])
        e = MOVE_EASE(p_move)
        w = lerp(hero_w, self.mark_w, e)
        cx = lerp(W / 2.0, self.mark_cx, e)
        cy = lerp(H / 2.0, self.mark_cy, e)
        return cx, cy, w

    def _clear_time(self, x_limit: float) -> float:
        for i in range(self.nframes):
            t = i / FPS
            cx, _cy, w = self.mark_state(t)
            if cx + w / 2.0 <= x_limit:
                return t
        return T_MOVE[1]

    # —— 单帧 ——
    def frame(self, t: float) -> Image.Image:
        t = max(0.0, t - self.lead)          # 开场纯底一拍：整条时间轴右移 LEAD
        canvas = Image.new("RGB", (W + 2 * PAD, H + 2 * PAD), self.bg)

        fly = self.plan["fly"]
        if fly is not None and t >= fly[0]:
            # B 版收尾：整条横排当作一张图，向左上角收起并缩小
            self._paste_lockup(canvas, self._fly_rect(t))
        else:
            zh_t = self._reveal(t, self.t_div + T_ZH_LAG, T_ZH_DUR)
            en_t = self._reveal(t, self.t_div + T_ZH_LAG + T_EN_LAG, T_EN_DUR)
            if zh_t > 0.0:
                self._paste_text(canvas, self.zh, self.zh_dy, zh_t)
            if en_t > 0.0:
                self._paste_text(canvas, self.en, self.en_dy, en_t)

            dv = seg(t, self.t_div, self.t_div + T_DIV_DUR)
            if dv > 0.0:
                self._paste_divider(canvas, out_cubic(dv))

            cx, cy, w = self.mark_state(t)
            if w > 1.0:
                self._paste_mark(canvas, cx, cy, w, 1.0)

        blur, op = self._envelope(t)
        if blur > 0.4:
            canvas = canvas.filter(ImageFilter.GaussianBlur(blur))
        canvas = canvas.crop((PAD, PAD, PAD + W, PAD + H))
        if op < 0.999:
            canvas = Image.blend(Image.new("RGB", (W, H), self.bg), canvas, op)
        return canvas

    def _envelope(self, t: float) -> tuple[float, float]:
        """整幅的虚化半径与不透明度（背景纯色，等价于只对图形做光学虚化）。"""
        blur, op = 0.0, 1.0
        if t < T_IN:                                  # 开场：从无到有淡入 + 焦点由虚到实
            p = seg(t, 0.0, T_IN)
            s = p * p * (3.0 - 2.0 * p)
            sp = seg(t, 0.0, OP_IN_DUR)               # 不透明度单独快速补满
            op = sp * sp * (3.0 - 2.0 * sp)
            blur = BLUR_IN * (1.0 - s) ** 1.3
        out = self.plan["out"]
        if out is not None and t > out[0]:            # A 版：S 曲线淡出
            p = seg(t, out[0], out[1])
            s = p * p * (3.0 - 2.0 * p)
            op = min(op, 1.0 - s)
            blur = max(blur, BLUR_OUT * s ** 1.3)
        fly = self.plan["fly"]
        if fly is not None:                           # B 版：起飞微虚、落位回实
            p = seg(t, fly[0], fly[1])
            if p < 0.30:
                blur = max(blur, BLUR_FLY * smoothstep(0.0, 0.30, p))
            else:
                blur = max(blur, BLUR_FLY * (1.0 - smoothstep(0.30, 1.0, p)))
        return blur, op

    def _fly_rect(self, t: float) -> tuple[float, float, float, float]:
        fly = self.plan["fly"]
        e = FLY_EASE(seg(t, fly[0], fly[1]))        # 专用飞行缓动：起手干脆、落位柔收
        return tuple(lerp(a, b, e) for a, b in zip(self.rect0, self.rect1))

    def _paste_lockup(self, canvas: Image.Image, rect: tuple[float, float, float, float]) -> None:
        x, y, w, h = rect
        tw, th = max(1, int(round(w))), max(1, int(round(h)))
        sprite = self.fly_sprite.resize((tw, th), Image.Resampling.LANCZOS, reducing_gap=3.0)
        canvas.paste(sprite, (int(round(x)) + PAD, int(round(y)) + PAD))

    def _reveal(self, t: float, t0: float, dur: float) -> float:
        return WIPE_EASE(seg(t, t0, t0 + dur))

    def _paste_text(self, canvas: Image.Image, sprite: Image.Image, dy: int, q: float) -> None:
        arr = np.array(sprite)
        alpha = arr[..., 3].astype(np.float32) / 255.0
        x0 = self.text_x
        # 起手时整体略偏左，随揭示右移归位 —— 字是"挪"出来的，不是单纯被擦出来
        dx = -70.0 * (1.0 - q)
        xs = x0 + dx + np.arange(arr.shape[1], dtype=np.float32) + 0.5
        # ⛔ 擦除头必须锚定在 **dx 偏移后的字头** 再退一个羽化宽度：q=0 时零暴露。
        #   旧写法把擦除头锚在最终位置 x0 —— 而 sprite 头被 dx=-70 推到了 x0-70，
        #   揭示第 0 帧就有 70px（半个"重"字）直接裸露，没有经过任何擦除过渡
        #   （用户 2026-11-03：「重字有一半在最开始就没有被遮盖住」）。
        wipe = (x0 + dx) - FEATHER + (self.text_w + 2.0 * FEATHER + 70.0) * q
        m = np.clip(((wipe + FEATHER) - xs) / (2.0 * FEATHER), 0.0, 1.0)
        m = m * m * (3.0 - 2.0 * m)
        arr[..., 3] = (alpha * m[None, :] * 255.0 + 0.5).astype(np.uint8)
        img = Image.fromarray(arr, "RGBA")
        canvas.paste(img, (int(round(x0 + dx)) + PAD, int(round(self.text_y)) + dy + PAD), img)

    def _paste_divider(self, canvas: Image.Image, e: float) -> None:
        x0, x1 = self.div_x0, self.div_x1
        py0 = int(np.floor(x0)) - 1
        px0 = int(np.floor(x0)) - 1
        cols = int(np.ceil(x1)) + 1 - px0
        ys, ye = self.div_y0, self.div_y1
        cy = (ys + ye) / 2.0
        hh = (ye - ys) / 2.0 * e
        top, bot = cy - hh, cy + hh
        if bot - top < 0.5:
            return
        ry0 = int(np.floor(top))
        rows = int(np.ceil(bot)) + 1 - ry0
        # 列/行覆盖率解析式，避免细线出现台阶
        xa = np.arange(px0, px0 + cols, dtype=np.float64)
        cov_x = np.clip(np.minimum(xa + 1.0, x1) - np.maximum(xa, x0), 0.0, 1.0)
        ya = np.arange(ry0, ry0 + rows, dtype=np.float64)
        cov_y = np.clip(np.minimum(ya + 1.0, bot) - np.maximum(ya, top), 0.0, 1.0)
        a = np.outer(cov_y, cov_x)
        rgba = np.zeros((rows, cols, 4), np.uint8)
        rgba[..., 0], rgba[..., 1], rgba[..., 2] = self.div_rgb
        rgba[..., 3] = (a * 255.0 + 0.5).astype(np.uint8)
        img = Image.fromarray(rgba, "RGBA")
        canvas.paste(img, (px0 + PAD, ry0 + PAD), img)
        del py0

    def _paste_mark(self, canvas: Image.Image, cx: float, cy: float, w: float, op: float) -> None:
        tw = max(1, int(round(w)))
        th = max(1, int(round(w * self.mark_ar)))
        sprite = self.mark_src.resize((tw, th), Image.Resampling.LANCZOS, reducing_gap=3.0)
        if op < 0.999:
            arr = np.array(sprite)
            arr[..., 3] = (arr[..., 3].astype(np.float32) * op).astype(np.uint8)
            sprite = Image.fromarray(arr, "RGBA")
        px = int(round(cx - tw / 2.0)) + PAD
        py = int(round(cy - th / 2.0)) + PAD
        canvas.paste(sprite, (px, py), sprite)


# ---------------------------------------------------------------- 输出
def encode(variant: str, plan: str) -> str:
    scene = Scene(variant, plan)
    os.makedirs(OUTDIR, exist_ok=True)
    suffix = "" if plan == "A" else "_planb"
    out = os.path.join(OUTDIR, "splash_%s%s_4k60.mp4" % (variant, suffix))
    # B 版落点导出归一化墨迹盒（机型无关），供播放器运行时做纠正相似变换
    if plan == "B":
        b = _land_ink_4k(variant)
        _write_land_box(variant, [b[0] / W, b[1] / H, b[2] / W, b[3] / H])
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H), "-r", str(FPS), "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "15",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "5.2",
        "-g", "120", "-keyint_min", "60",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-movflags", "+faststart", out,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for i in range(scene.nframes):
        img = scene.frame(i / FPS)
        proc.stdin.write(img.tobytes())
        if i % 30 == 0:
            print("  %s/%s  %3d/%d" % (variant, plan, i, scene.nframes), flush=True)
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("ffmpeg 失败")
    print("  -> %s  (%.1f MB)" % (out, os.path.getsize(out) / 1048576))
    return out


def probe(plan: str) -> None:
    os.makedirs(PROBE, exist_ok=True)
    tag = "" if plan == "A" else "_%s" % plan
    scene = Scene("light", plan)
    d = scene.dur
    times = [d * i / 12 for i in range(13)]
    for t in times:
        img = scene.frame(t).resize((W // 3, H // 3), Image.Resampling.LANCZOS)
        img.save(os.path.join(PROBE, "p%s_t%05.2f.png" % (tag, t)))
    for variant in ("light", "dark"):
        s = Scene(variant, plan)
        s.frame(2.60).save(os.path.join(PROBE, "settled_%s%s.png" % (variant, tag)))
        s.frame(scene.dur - 0.05).save(os.path.join(PROBE, "landed_%s%s.png" % (variant, tag)))
    print("probe(%s) -> %s   dur=%.2f  frames=%d  rect1=%s"
          % (plan, PROBE, d, scene.nframes,
             tuple(round(v, 1) for v in scene.rect1)))


if __name__ == "__main__":
    argv = sys.argv[1:]
    mode = argv[0] if argv else "probe"
    plan = "A"
    rest = argv[1:]
    if rest and rest[0].upper() in PLANS:
        plan = rest.pop(0).upper()
    if mode == "probe":
        probe(plan)
    elif mode == "render":
        for v in (rest or ["light", "dark"]):
            encode(v, plan)
    elif mode == "landbox":
        # 只重算 _land_box.json（墨迹盒），不重编码 mp4 —— 视频落点画面不变，
        # 仅元数据随渲染几何更新（见 land_box_for）。
        for v in (rest or ["light", "dark"]):
            box = land_box_for(v)
            _write_land_box(v, box)
            print(f"  {v}_planb land_box(ink) = {[round(x, 6) for x in box]}")
    else:
        raise SystemExit("unknown mode: %s" % mode)
